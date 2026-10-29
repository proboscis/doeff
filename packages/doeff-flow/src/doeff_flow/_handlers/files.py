"""The handler that answers ReadTraces from the trace files, through doeff's file effects.

The trace of a workflow is ``{trace_dir}/{workflow_id}/trace.jsonl`` (written by
``doeff_flow._trace``). This handler reads it with the foundation file effects
(StatPath, ListDirectory, ReadText), so the handler above it decides where the files
are: ``os_file_handler`` for the real files, ``memory_file_handler`` in tests.
"""

from pathlib import Path
from typing import Any

from doeff_core_effects.file_effects import (
    FileFailed,
    ListDirectory,
    PathKind,
    ReadText,
    StatPath,
    file_done,
)

from doeff import Effect, Pass, Resume, do, handler
from doeff_flow._effects import ReadTraces
from doeff_flow._trace import LiveTrace, _trace_from_line

TRACE_FILE = "trace.jsonl"


@do
def _trace_files(trace_dir: Path):
    """The trace file of each directory under ``trace_dir``, in name order.

    None when ``trace_dir`` is not a directory (a trace directory no workflow wrote yet).
    """
    found = yield file_done(StatPath(str(trace_dir)))
    if found.kind is not PathKind.DIRECTORY:
        return ()
    listed = yield file_done(ListDirectory(str(trace_dir)))
    workflow_dirs = (PathKind.DIRECTORY, PathKind.SYMLINK)
    names = sorted(entry.name for entry in listed if entry.kind in workflow_dirs)
    return tuple(trace_dir / name / TRACE_FILE for name in names)


@do
def _entries(trace_file: Path):
    """The trace entries ``trace_file`` holds, oldest first.

    None when there is no such file — the path is missing, or its directory is a
    symlink to something that is not a directory (StatPath refuses it). A line that is
    not a trace entry (one still being written) is left out.
    """
    found = yield StatPath(str(trace_file))
    if isinstance(found, FileFailed) or found.kind is not PathKind.FILE:
        return ()
    text = yield file_done(ReadText(str(trace_file)))
    entries = (_trace_from_line(line) for line in text.splitlines())
    return tuple(entry for entry in entries if entry is not None)


@do
def _read_traces(effect: ReadTraces):
    trace_files = (
        (effect.trace_dir / effect.workflow_id / TRACE_FILE,)
        if effect.workflow_id is not None
        else (yield _trace_files(effect.trace_dir))
    )
    traces: tuple[LiveTrace, ...] = ()
    for trace_file in trace_files:
        traces = (*traces, *(yield _entries(trace_file)))
    return traces


@handler
def trace_files_handler(effect: Effect, k: Any):
    """Answer ReadTraces from ``{trace_dir}/{workflow_id}/trace.jsonl`` with the file effects."""
    if isinstance(effect, ReadTraces):
        traces = yield _read_traces(effect)
        return (yield Resume(k, traces))
    return (yield Pass(effect, k))


__all__ = ["trace_files_handler"]
