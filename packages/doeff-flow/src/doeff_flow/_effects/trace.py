"""Effect definitions for trace and observability operations."""


from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doeff import EffectBase
from doeff_flow._trace import LiveTrace


@dataclass(frozen=True, kw_only=True)
class TraceEffectBase(EffectBase):
    """Base class for doeff-flow trace effects."""


@dataclass(frozen=True, kw_only=True)
class TracePush(TraceEffectBase):
    """Push a trace span."""

    name: str
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class TraceAnnotate(TraceEffectBase):
    """Annotate current trace span."""

    key: str
    value: Any


@dataclass(frozen=True, kw_only=True)
class TraceSnapshot(TraceEffectBase):
    """Capture a trace snapshot."""

    label: str


@dataclass(frozen=True, kw_only=True)
class TraceCapture(TraceEffectBase):
    """Capture trace output from the active trace handler."""

    format: str = "json"


@dataclass(frozen=True, kw_only=True)
class ReadTraces(TraceEffectBase, EffectBase[tuple[LiveTrace, ...]]):
    """Read the trace entries the workflows wrote under a trace directory.

    Answers the entries of every workflow directory under ``trace_dir`` that holds a
    ``trace.jsonl`` — workflow by workflow in name order, each one's entries oldest
    first — or, with ``workflow_id``, that workflow's entries only. A trace directory
    or a workflow that wrote no trace answers no entries. A line that is not a trace
    entry (one still being written) is left out.
    """

    trace_dir: Path
    workflow_id: str | None = None


__all__ = [
    "ReadTraces",
    "TraceAnnotate",
    "TraceCapture",
    "TraceEffectBase",
    "TracePush",
    "TraceSnapshot",
]
