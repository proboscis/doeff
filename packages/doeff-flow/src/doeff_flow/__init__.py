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

from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from doeff_flow._trace import (
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
    """Run a program under a stack of reference handlers and return Ok / Err.

    The stack is the same as ``doeff.cli.run_services.default_interpreter``
    (reader, state, writer, try, slog, listen, await, scheduler), with the
    caller's ``env`` seeded into Ask and ``store`` into Get/Put, plus
    doeff-time's ``async_time_handler()`` inside ``await_handler()``: a program
    that yields ``Delay`` / ``GetTime`` waits on the wall clock. An exception
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
        await_handler(),
        # Innermost: it answers a time effect with an Await, which await_handler() above answers.
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


__all__ = [
    "LiveTrace",
    "MockTraceRecorder",
    "ProductionTraceRecorder",
    "ReadTraces",
    "TraceAnnotate",
    "TraceCapture",
    "TraceEffectBase",
    "TraceFrame",
    "TracePush",
    "TraceSnapshot",
    "get_default_trace_dir",
    "mock_handlers",
    "production_handlers",
    "run_result",
    "run_workflow",
    "trace_files_handler",
    "trace_observer",
    "validate_workflow_id",
]


# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers. A root that already has `__all__`
# lists the names there too; the tool does not add `__all__` to a root without one (DOEFF021).
if TYPE_CHECKING:
    from doeff_flow._effects.trace import ReadTraces as ReadTraces
    from doeff_flow._effects.trace import TraceAnnotate as TraceAnnotate
    from doeff_flow._effects.trace import TraceCapture as TraceCapture
    from doeff_flow._effects.trace import TraceEffectBase as TraceEffectBase
    from doeff_flow._effects.trace import TracePush as TracePush
    from doeff_flow._effects.trace import TraceSnapshot as TraceSnapshot
    from doeff_flow._handlers.files import trace_files_handler as trace_files_handler
    from doeff_flow._handlers.production import ProductionTraceRecorder as ProductionTraceRecorder
    from doeff_flow._handlers.production import production_handlers as production_handlers
    from doeff_flow._handlers.testing import MockTraceRecorder as MockTraceRecorder
    from doeff_flow._handlers.testing import mock_handlers as mock_handlers

_LAZY_EXPORTS: dict[str, str] = {
    "ReadTraces": "doeff_flow._effects.trace",
    "TraceAnnotate": "doeff_flow._effects.trace",
    "TraceCapture": "doeff_flow._effects.trace",
    "TraceEffectBase": "doeff_flow._effects.trace",
    "TracePush": "doeff_flow._effects.trace",
    "TraceSnapshot": "doeff_flow._effects.trace",
    "MockTraceRecorder": "doeff_flow._handlers.testing",
    "ProductionTraceRecorder": "doeff_flow._handlers.production",
    "mock_handlers": "doeff_flow._handlers.testing",
    "production_handlers": "doeff_flow._handlers.production",
    "trace_files_handler": "doeff_flow._handlers.files",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
