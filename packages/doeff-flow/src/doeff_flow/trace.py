"""
Live effect trace observability for doeff workflows.

This module provides types and utilities for observing workflow execution
in real-time, writing trace data to JSONL files that can be watched by
external tools.

Public API:
    - TraceFrame: A frame in the effect trace (corresponds to a @do function call)
    - LiveTrace: Current execution state of a workflow
    - trace_observer: Context manager for creating an on_step callback

Example usage:
    from doeff.cesk import run_sync
    from doeff_flow.trace import trace_observer

    with trace_observer("wf-001", Path(".doeff-flow")) as on_step:
        result = run_sync(my_workflow(), on_step=on_step)
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Generator

if TYPE_CHECKING:
    from doeff.cesk_observability import ExecutionSnapshot


@dataclass
class TraceFrame:
    """A frame in the effect trace (corresponds to a @do function call).

    Attributes:
        function: Function name.
        file: Source file path.
        line: Line number in the source file.
        code: Optional source code at that line.
    """

    function: str
    file: str
    line: int
    code: str | None


@dataclass
class LiveTrace:
    """Current execution state of a workflow.

    Attributes:
        workflow_id: Unique identifier for the workflow.
        step: Current interpreter step count.
        status: Execution status ("pending" | "running" | "paused" | "completed" | "failed").
        current_effect: String representation of the current effect being processed.
        trace: List of trace frames (K stack, innermost last).
        started_at: ISO timestamp of workflow start.
        updated_at: ISO timestamp of last update.
        error: Error message if status is "failed", None otherwise.
    """

    workflow_id: str
    step: int
    status: str  # "pending" | "running" | "paused" | "completed" | "failed"
    current_effect: str | None
    trace: list[TraceFrame]
    started_at: str
    updated_at: str
    error: str | None = None


def _validate_workflow_id(workflow_id: str) -> str:
    """Validate workflow_id contains only safe characters.

    Args:
        workflow_id: The workflow ID to validate.

    Returns:
        The validated workflow ID.

    Raises:
        ValueError: If the workflow ID contains invalid characters.
    """
    if not re.match(r"^[a-zA-Z0-9_-]+$", workflow_id):
        raise ValueError(
            f"Invalid workflow_id: {workflow_id!r}. Must match [a-zA-Z0-9_-]+"
        )
    return workflow_id


def _safe_repr(obj: object, max_len: int = 200) -> str:
    """Safe repr with truncation for large objects.

    Args:
        obj: The object to represent.
        max_len: Maximum length of the representation.

    Returns:
        String representation, truncated if necessary.
    """
    r = repr(obj)
    if len(r) > max_len:
        return r[: max_len - 3] + "..."
    return r


def _write_trace(trace_file: Path, trace: LiveTrace) -> None:
    """Append trace as JSONL line.

    Args:
        trace_file: Path to the trace JSONL file.
        trace: The LiveTrace to write.
    """
    # Convert dataclass to dict, handling nested TraceFrame dataclasses
    trace_dict = {
        "workflow_id": trace.workflow_id,
        "step": trace.step,
        "status": trace.status,
        "current_effect": trace.current_effect,
        "trace": [asdict(frame) for frame in trace.trace],
        "started_at": trace.started_at,
        "updated_at": trace.updated_at,
        "error": trace.error,
    }
    with trace_file.open("a") as f:
        f.write(json.dumps(trace_dict) + "\n")


@contextmanager
def trace_observer(
    workflow_id: str,
    trace_dir: Path,
) -> Generator[Callable[["ExecutionSnapshot"], None], None, None]:
    """Context manager that creates an on_step callback for live trace.

    Creates a callback function that writes execution snapshots to a JSONL
    file, enabling real-time observation of workflow execution.

    Args:
        workflow_id: Unique identifier for the workflow. Must match [a-zA-Z0-9_-]+.
        trace_dir: Directory where trace files will be written.

    Yields:
        A callback function suitable for passing to run_sync(on_step=...).

    Example:
        from doeff.cesk import run_sync
        from doeff_flow.trace import trace_observer

        with trace_observer("wf-001", Path(".doeff-flow")) as on_step:
            result = run_sync(my_workflow(), on_step=on_step)
    """
    workflow_id = _validate_workflow_id(workflow_id)
    trace_file = trace_dir / workflow_id / "trace.jsonl"
    trace_file.parent.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now().isoformat()

    def on_step(snapshot: "ExecutionSnapshot") -> None:
        # Extract ReturnFrames only (these are @do function calls)
        frames = [
            TraceFrame(
                function=f.location.function if f.location else "?",
                file=f.location.filename if f.location else "?",
                line=f.location.line if f.location else 0,
                code=f.location.code if f.location else None,
            )
            for f in snapshot.k_stack
            if f.frame_type == "ReturnFrame"
        ]

        trace = LiveTrace(
            workflow_id=workflow_id,
            step=snapshot.step_count,
            status=snapshot.status,
            current_effect=(
                _safe_repr(snapshot.current_effect) if snapshot.current_effect else None
            ),
            trace=frames,
            started_at=started_at,
            updated_at=datetime.now().isoformat(),
        )

        _write_trace(trace_file, trace)

    yield on_step
    # No cleanup needed - final state already written by last on_step call


__all__ = [
    "TraceFrame",
    "LiveTrace",
    "trace_observer",
]
