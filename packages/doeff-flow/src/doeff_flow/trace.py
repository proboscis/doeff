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
import os
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Generator

if TYPE_CHECKING:
    from doeff.cesk_observability import ExecutionSnapshot


def get_default_trace_dir() -> Path:
    """Get the default trace directory following XDG Base Directory Specification.

    Returns the trace directory in this order of precedence:
    1. $DOEFF_FLOW_TRACE_DIR if set
    2. $XDG_STATE_HOME/doeff-flow if XDG_STATE_HOME is set
    3. ~/.local/state/doeff-flow (XDG default)

    Returns:
        Path to the default trace directory.
    """
    # Check for explicit override
    if env_dir := os.environ.get("DOEFF_FLOW_TRACE_DIR"):
        return Path(env_dir)

    # Use XDG_STATE_HOME or default
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        base = Path(xdg_state_home)
    else:
        base = Path.home() / ".local" / "state"

    return base / "doeff-flow"


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


def validate_workflow_id(workflow_id: str) -> str:
    """Validate workflow_id contains only safe characters and reasonable length.

    Args:
        workflow_id: The workflow ID to validate.

    Returns:
        The validated workflow ID.

    Raises:
        ValueError: If the workflow ID is invalid (empty, too long, or has invalid characters).
    """
    if not workflow_id:
        raise ValueError("workflow_id cannot be empty")
    if len(workflow_id) > 255:
        raise ValueError(f"workflow_id too long: {len(workflow_id)} > 255 characters")
    if not re.match(r"^[a-zA-Z0-9_-]+$", workflow_id):
        raise ValueError(
            f"Invalid workflow_id: {workflow_id!r}. Must match [a-zA-Z0-9_-]+"
        )
    return workflow_id


# Alias for internal use
_validate_workflow_id = validate_workflow_id


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
    trace_dir: Path | str | None = None,
) -> Generator[Callable[["ExecutionSnapshot"], None], None, None]:
    """Context manager that creates an on_step callback for live trace.

    Creates a callback function that writes execution snapshots to a JSONL
    file, enabling real-time observation of workflow execution.

    Args:
        workflow_id: Unique identifier for the workflow. Must match [a-zA-Z0-9_-]+.
        trace_dir: Directory where trace files will be written. Can be Path or str.
            If None, uses XDG-compliant default (~/.local/state/doeff-flow).

    Yields:
        A callback function suitable for passing to run_sync(on_step=...).

    Example:
        from doeff.cesk import run_sync
        from doeff_flow.trace import trace_observer

        # Uses default XDG directory
        with trace_observer("wf-001") as on_step:
            result = run_sync(my_workflow(), on_step=on_step)
    """
    workflow_id = _validate_workflow_id(workflow_id)
    if trace_dir is None:
        trace_dir = get_default_trace_dir()
    elif isinstance(trace_dir, str):
        trace_dir = Path(trace_dir)
    trace_file = trace_dir / workflow_id / "trace.jsonl"
    trace_file.parent.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now().isoformat()

    def on_step(snapshot: "ExecutionSnapshot") -> None:
        # For error cases, use the captured effect trace and error location
        if snapshot.error is not None and snapshot.error.effect_trace:
            # Build frames from the captured effect trace
            frames = [
                TraceFrame(
                    function=loc.function,
                    file=loc.filename,
                    line=loc.line,
                    code=loc.code,
                )
                for loc in snapshot.error.effect_trace
            ]
            # Update the deepest frame with error location (where exception was raised)
            if snapshot.error.error_location is not None and frames:
                error_loc = snapshot.error.error_location
                # If error is in the same function as deepest frame, update that frame
                # (shows the raise line instead of the yield line)
                if frames[-1].function == error_loc.function:
                    frames[-1] = TraceFrame(
                        function=error_loc.function,
                        file=error_loc.filename,
                        line=error_loc.line,
                        code=error_loc.code,
                    )
                # If error is in a different function (pure Python call), add as new frame
                elif frames[-1].line != error_loc.line:
                    frames.append(
                        TraceFrame(
                            function=error_loc.function,
                            file=error_loc.filename,
                            line=error_loc.line,
                            code=error_loc.code,
                        )
                    )
        else:
            # Normal case: extract ReturnFrames from K stack
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

            # Include active_call as the deepest frame (captures non-yielding functions)
            if snapshot.active_call is not None:
                frames.append(
                    TraceFrame(
                        function=snapshot.active_call.function,
                        file=snapshot.active_call.filename,
                        line=snapshot.active_call.line,
                        code=snapshot.active_call.code,
                    )
                )

        # Extract error info if present
        error_msg = None
        if snapshot.error is not None:
            error_msg = f"{snapshot.error.exception_type}: {snapshot.error.message}"

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
            error=error_msg,
        )

        _write_trace(trace_file, trace)

    yield on_step
    # No cleanup needed - final state already written by last on_step call


__all__ = [
    "TraceFrame",
    "LiveTrace",
    "trace_observer",
    "validate_workflow_id",
    "get_default_trace_dir",
]
