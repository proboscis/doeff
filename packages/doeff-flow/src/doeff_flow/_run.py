"""Run a program under the reference handlers and write its terminal trace (doeff-flow's own tests and examples).

Not part of the public API (ADR-DOE-API-001 R1 — a runner is neither an effect nor a handler): a program that
records its trace yields doeff-flow's trace effects under ``production_handlers(workflow_id)``.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from doeff_flow._trace import get_default_trace_dir, trace_observer

if TYPE_CHECKING:
    from doeff import Err, Ok, Program

T = TypeVar("T")


def run_result(
    program: "Program[T]",
    *,
    env: dict[Any, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> "Ok[T] | Err":
    """Run a program under a stack of reference handlers and return Ok / Err.

    The stack is the same as ``doeff.cli.run_services.default_interpreter``
    (reader, state, writer, try, slog, listen, await, scheduler), with the
    caller's ``env`` seeded into Ask and ``store`` into Get/Put, plus
    doeff-time's ``async_time_handler()`` inside ``await_handler``: a program
    that yields ``Delay`` / ``GetTime`` waits on the wall clock. An exception
    raised by the program is returned as ``Err(error)`` instead of propagating,
    so the value can be handed to ``write_terminal_trace``.
    """
    from doeff_core_effects import (
        Try,
        await_handler,
        lazy_ask,
        listen_handler,
        scheduled,
        slog_handler,
        state,
        try_handler,
        writer,
    )
    from doeff_time import async_time_handler

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
        await_handler,
        # Innermost: it answers a time effect with an Await, which await_handler above answers.
        async_time_handler(),
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
        from doeff_flow._run import run_workflow

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
    from doeff_flow._trace import write_terminal_trace

    if trace_dir is None:
        trace_dir = get_default_trace_dir()
    elif isinstance(trace_dir, str):
        trace_dir = Path(trace_dir)

    with trace_observer(workflow_id, trace_dir) as on_step:
        _ = on_step
        result = run_result(program, env=env, store=store)
        write_terminal_trace(workflow_id, trace_dir, result)
        return result
