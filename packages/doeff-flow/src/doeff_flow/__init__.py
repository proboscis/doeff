"""
doeff-flow: Live Effect Trace Observability

This package provides tools for observing doeff workflow execution in real-time.

Features:
    - Write live effect trace to JSONL files during workflow execution
    - CLI commands to watch traces in real-time
    - Support for multiple concurrent workflows
    - XDG-compliant default trace directory (~/.local/state/doeff-flow)

Quick Start:
    # Option A: Convenience wrapper (uses XDG default directory)
    from doeff_flow import run_workflow

    result = run_workflow(
        my_workflow(),
        workflow_id="wf-001",
    )

    # Option B: Composable with existing run
    from doeff import run
    from doeff_flow import trace_observer

    with trace_observer("wf-001") as on_step:
        result = run(my_workflow())

CLI Usage:
    # Watch all workflows
    $ doeff-flow watch

    # Watch single workflow
    $ doeff-flow watch wf-001

    # List active workflows
    $ doeff-flow ps

    # Show history
    $ doeff-flow history wf-001
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from doeff_flow.trace import (
    LiveTrace,
    TraceFrame,
    get_default_trace_dir,
    trace_observer,
    validate_workflow_id,
)

if TYPE_CHECKING:
    from doeff import Program, RunResult

T = TypeVar("T")


def run_workflow(
    program: Program[T],
    workflow_id: str,
    trace_dir: Path | str | None = None,
    *,
    env: dict[Any, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> RunResult[T]:
    """Run a workflow with live trace observability.

    Convenience wrapper that combines run with trace output.
    The trace is written to {trace_dir}/{workflow_id}/trace.jsonl.

    Args:
        program: The doeff program to execute.
        workflow_id: Unique identifier for this workflow run.
            Must match [a-zA-Z0-9_-]+.
        trace_dir: Directory where trace files will be written.
            If None, uses XDG-compliant default (~/.local/state/doeff-flow).
        env: Initial environment (optional).
        store: Initial store (optional).

    Returns:
        RunResult containing the execution result.

    Example:
        from doeff import do
        from doeff import Pure
        from doeff_flow import run_workflow

        @do
        def my_workflow():
            x = yield Pure(10)
            y = yield Pure(20)
            return x + y

        # Uses XDG default trace directory
        result = run_workflow(
            my_workflow(),
            workflow_id="example-001",
        )
        print(result.value)  # 30
    """
    from doeff import run as run_sync
    from doeff_flow.trace import write_terminal_trace

    if trace_dir is None:
        trace_dir = get_default_trace_dir()
    elif isinstance(trace_dir, str):
        trace_dir = Path(trace_dir)

    with trace_observer(workflow_id, trace_dir) as on_step:
        _ = on_step
        result = run_sync(program, env=env, store=store)
        write_terminal_trace(workflow_id, trace_dir, result)
        return result


__all__ = [
    "LiveTrace",
    # Core types
    "TraceFrame",
    # XDG support
    "get_default_trace_dir",
    # Convenience wrapper
    "run_workflow",
    # Observer
    "trace_observer",
    # Validation
    "validate_workflow_id",
]
