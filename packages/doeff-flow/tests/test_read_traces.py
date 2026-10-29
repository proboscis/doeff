"""ReadTraces under trace_files_handler, and the CLI's programs over it.

Only the handlers that touch the outside world are swapped: memory_file_handler for the
trace files, sim_time_handler for the clock and a recorder for the terminal's ShowPanel.
The CLI's programs and trace_files_handler are the ones the console script runs.
"""

import json
from pathlib import Path
from typing import Any

from doeff_core_effects import scheduled, state
from doeff_core_effects.file_effects import MemoryFile, MemoryFiles
from doeff_core_effects.memory_file import memory_file_handler
from doeff_flow import ReadTraces, trace_files_handler
from doeff_flow._cli import ShowPanel, _history_table, _ps_table, _watch_all, _watch_single
from doeff_time import sim_time_handler

from doeff import Effect, Pass, Resume, handler, run, with_handlers

TRACE_DIR = Path("/traces")


def _line(workflow_id: str, step: int, status: str, **fields: Any) -> str:
    return json.dumps(
        {
            "workflow_id": workflow_id,
            "step": step,
            "status": status,
            "current_effect": f"Pure({step})",
            "trace": [],
            "started_at": "2026-10-27T00:00:00",
            "updated_at": f"2026-10-27T00:00:0{step}",
            "error": None,
            "result": None,
            "gather": None,
            "last_slog": None,
            **fields,
        }
    )


def _files(*traces: tuple[str, list[str]], extra_dirs: tuple[str, ...] = ()) -> MemoryFiles:
    """A trace directory holding each (workflow id, lines) as its trace.jsonl."""
    return MemoryFiles(
        files=tuple(
            MemoryFile(
                path=f"{TRACE_DIR}/{workflow_id}/trace.jsonl", content="".join(lines).encode()
            )
            for workflow_id, lines in traces
        )
        + (MemoryFile(path=f"{TRACE_DIR}/notes.txt", content=b"not a workflow"),),
        dirs=(
            str(TRACE_DIR),
            *(f"{TRACE_DIR}/{workflow_id}" for workflow_id, _ in traces),
            *extra_dirs,
        ),
    )


def _run(program: Any, files: MemoryFiles, *handlers: Any) -> Any:
    stack = [
        state(),
        memory_file_handler(files),
        sim_time_handler(),
        *handlers,
        trace_files_handler,
    ]
    return run(scheduled(with_handlers(stack, program)))


def _shown_into(shown: list[Any]):
    @handler
    def record_panels(effect: Effect, k: Any):
        if isinstance(effect, ShowPanel):
            shown.append(effect.renderable)
            return (yield Resume(k, None))
        return (yield Pass(effect, k))

    return record_panels


def test_read_traces_answers_every_workflow_in_name_order_oldest_first():
    files = _files(
        ("wf-b", [_line("wf-b", 1, "running") + "\n"]),
        ("wf-a", [_line("wf-a", 1, "running") + "\n", _line("wf-a", 2, "completed") + "\n"]),
        extra_dirs=(f"{TRACE_DIR}/wf-empty",),
    )

    entries = _run(ReadTraces(trace_dir=TRACE_DIR), files)

    assert [(entry.workflow_id, entry.step, entry.status) for entry in entries] == [
        ("wf-a", 1, "running"),
        ("wf-a", 2, "completed"),
        ("wf-b", 1, "running"),
    ]


def test_read_traces_of_one_workflow_leaves_out_a_line_still_being_written():
    files = _files(("wf-a", [_line("wf-a", 1, "running") + "\n", '{"workflow_id": "wf-a", "st']))

    entries = _run(ReadTraces(trace_dir=TRACE_DIR, workflow_id="wf-a"), files)

    assert [entry.step for entry in entries] == [1]


def test_read_traces_of_a_missing_directory_or_workflow_answers_no_entries():
    files = _files(("wf-a", [_line("wf-a", 1, "running") + "\n"]))

    assert _run(ReadTraces(trace_dir=Path("/elsewhere")), files) == ()
    assert _run(ReadTraces(trace_dir=TRACE_DIR, workflow_id="wf-missing"), files) == ()


def test_read_traces_types_the_frames_and_the_gather():
    frame = {"function": "step_one", "file": "/src/flow.py", "line": 12, "code": "yield x"}
    gather = {"total": 3, "completed": 1, "results": ["'a'"]}
    files = _files(("wf-a", [_line("wf-a", 1, "running", trace=[frame], gather=gather) + "\n"]))

    (entry,) = _run(ReadTraces(trace_dir=TRACE_DIR, workflow_id="wf-a"), files)

    assert entry.trace[0].function == "step_one"
    assert entry.trace[0].line == 12
    assert entry.gather is not None
    assert (entry.gather.total, entry.gather.completed, entry.gather.results) == (3, 1, ["'a'"])


def test_watch_single_shows_the_latest_entry_and_returns_when_it_finished():
    files = _files(
        ("wf-a", [_line("wf-a", 1, "running") + "\n", _line("wf-a", 2, "completed") + "\n"])
    )
    shown: list[Any] = []

    finished = _run(
        _watch_single(TRACE_DIR, "wf-a", exit_on_complete=True, poll_interval=0.5),
        files,
        _shown_into(shown),
    )

    assert finished.status == "completed"
    assert finished.step == 2
    assert len(shown) == 1


def test_watch_all_returns_when_every_workflow_finished():
    files = _files(
        ("wf-a", [_line("wf-a", 1, "completed") + "\n"]),
        ("wf-b", [_line("wf-b", 3, "failed", error="ValueError: boom") + "\n"]),
    )
    shown: list[Any] = []

    finished = _run(
        _watch_all(TRACE_DIR, exit_on_complete=True, poll_interval=0.5), files, _shown_into(shown)
    )

    assert [(entry.workflow_id, entry.status) for entry in finished] == [
        ("wf-a", "completed"),
        ("wf-b", "failed"),
    ]
    assert len(shown) == 1


def test_ps_and_history_tables_read_through_read_traces():
    files = _files(
        ("wf-a", [_line("wf-a", step, "running") + "\n" for step in range(1, 6)]),
        ("wf-b", [_line("wf-b", 1, "completed") + "\n"]),
    )

    ps = _run(_ps_table(TRACE_DIR), files)
    history = _run(_history_table(TRACE_DIR, "wf-a", 3), files)

    assert ps.row_count == 2
    assert history.row_count == 3
    assert _run(_ps_table(Path("/elsewhere")), files) is None
    assert _run(_history_table(TRACE_DIR, "wf-missing", 3), files) is None
