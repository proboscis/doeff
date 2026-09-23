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
    from doeff import Err, Ok, Program

T = TypeVar("T")


def run_result(
    program: "Program[T]",
    *,
    env: dict[Any, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> "Ok[T] | Err":
    """Run a program under the standard handler stack and return Ok / Err.

    The stack is the same as ``doeff.cli.run_services.default_interpreter``
    (reader, state, writer, try, slog, listen, await, scheduler), with the
    caller's ``env`` seeded into Ask and ``store`` into Get/Put. An exception
    raised by the program is returned as ``Err(error)`` instead of propagating,
    so the value can be handed to ``write_terminal_trace``.
    """
    from doeff_core_effects import Try
    from doeff_core_effects.handlers import (
        await_handler,
        lazy_ask,
        listen_handler,
        slog_handler,
        state,
        try_handler,
        writer,
    )
    from doeff_core_effects.scheduler import scheduled

    from doeff import do, run

    @do
    def _captured():
        return (yield Try(program))

    handlers = [
        lazy_ask(env),
        state(store),
        writer,
        try_handler,
        slog_handler,
        listen_handler,
        await_handler(),
    ]
    wrapped = _captured()
    for h in reversed(handlers):
        wrapped = h(wrapped)
    return run(scheduled(wrapped))


def run_workflow(
    program: "Program[T]",
    workflow_id: str,
    trace_dir: Path | str | None = None,
    *,
    env: dict[Any, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> "Ok[T] | Err":
    """Run a workflow with live trace observability.

    Convenience wrapper that combines run with trace output.
    The trace is written to {trace_dir}/{workflow_id}/trace.jsonl.

    Args:
        program: The doeff program to execute.
        workflow_id: Unique identifier for this workflow run.
            Must match [a-zA-Z0-9_-]+.
        trace_dir: Directory where trace files will be written.
            If None, uses XDG-compliant default (~/.local/state/doeff-flow).
        env: Initial environment for Ask (optional).
        store: Initial state for Get/Put (optional).

    Returns:
        ``Ok(value)`` on success, ``Err(error)`` when the program raised.

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
    from doeff_flow.trace import write_terminal_trace

    if trace_dir is None:
        trace_dir = get_default_trace_dir()
    elif isinstance(trace_dir, str):
        trace_dir = Path(trace_dir)

    with trace_observer(workflow_id, trace_dir) as on_step:
        _ = on_step
        result = run_result(program, env=env, store=store)
        write_terminal_trace(workflow_id, trace_dir, result)
        return result


__all__ = [
    "LiveTrace",
    # Core types
    "TraceFrame",
    # XDG support
    "get_default_trace_dir",
    # Convenience wrappers
    "run_result",
    "run_workflow",
    # Observer
    "trace_observer",
    # Validation
    "validate_workflow_id",
]
