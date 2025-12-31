"""
doeff-flow: Live Effect Trace Observability

This package provides tools for observing doeff workflow execution in real-time.

Features:
    - Write live effect trace to JSONL files during workflow execution
    - CLI commands to watch traces in real-time
    - Support for multiple concurrent workflows

Quick Start:
    # Option A: Convenience wrapper
    from doeff_flow import run_workflow

    result = run_workflow(
        my_workflow(),
        workflow_id="wf-001",
        trace_dir=Path(".doeff-flow"),
    )

    # Option B: Composable with existing run_sync
    from doeff.cesk import run_sync
    from doeff_flow import trace_observer

    with trace_observer("wf-001", Path(".doeff-flow")) as on_step:
        result = run_sync(my_workflow(), on_step=on_step)

CLI Usage:
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

from doeff_flow.trace import LiveTrace, TraceFrame, trace_observer

if TYPE_CHECKING:
    from doeff.cesk import CESKResult, Environment, Store
    from doeff.program import Program
    from doeff.storage import DurableStorage

T = TypeVar("T")


def run_workflow(
    program: "Program[T]",
    workflow_id: str,
    trace_dir: Path | str = Path(".doeff-flow"),
    *,
    env: "Environment | dict[Any, Any] | None" = None,
    store: "Store | None" = None,
    storage: "DurableStorage | None" = None,
) -> "CESKResult[T]":
    """Run a workflow with live trace observability.

    Convenience wrapper that combines run_sync with trace_observer.
    The trace is written to {trace_dir}/{workflow_id}/trace.jsonl.

    Args:
        program: The doeff program to execute.
        workflow_id: Unique identifier for this workflow run.
            Must match [a-zA-Z0-9_-]+.
        trace_dir: Directory where trace files will be written.
            Defaults to ".doeff-flow".
        env: Initial environment (optional).
        store: Initial store (optional).
        storage: Optional durable storage backend for cache effects.

    Returns:
        CESKResult containing the execution result.

    Example:
        from doeff import do
        from doeff.effects import Pure
        from doeff_flow import run_workflow

        @do
        def my_workflow():
            x = yield Pure(10)
            y = yield Pure(20)
            return x + y

        result = run_workflow(
            my_workflow(),
            workflow_id="example-001",
        )
        print(result.value)  # 30
    """
    from doeff.cesk import run_sync

    if isinstance(trace_dir, str):
        trace_dir = Path(trace_dir)

    with trace_observer(workflow_id, trace_dir) as on_step:
        return run_sync(
            program,
            env=env,
            store=store,
            storage=storage,
            on_step=on_step,
        )


__all__ = [
    # Core types
    "TraceFrame",
    "LiveTrace",
    # Observer
    "trace_observer",
    # Convenience wrapper
    "run_workflow",
]
