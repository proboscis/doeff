"""Function-based API for running doeff programs.

This module provides simple functions for running programs:
- sync_run: Run synchronously, handles async via background thread
- async_run: Run asynchronously, handles async via await

Handler presets are provided for common use cases:
- sync_handlers_preset: For sync_run (includes sync_await_handler)
- async_handlers_preset: For async_run (includes python_async_syntax_escape_handler)

Example:
    from doeff.cesk.run import sync_run, sync_handlers_preset
    from doeff.do import do

    @do
    def my_program():
        yield Put("x", 42)
        return (yield Get("x"))

    result = sync_run(my_program(), sync_handlers_preset)
    print(result.value)  # 42
"""

from __future__ import annotations

import asyncio
import queue
from typing import TYPE_CHECKING, Any, TypeVar, cast

from doeff._vendor import Err, FrozenDict, Ok
from doeff.cesk.errors import UnhandledEffectError
from doeff.cesk.handler_frame import Handler, WithHandler
from doeff.cesk.handlers.async_external_wait_handler import async_external_wait_handler
from doeff.cesk.handlers.atomic_handler import atomic_handler
from doeff.cesk.handlers.cache_handler import cache_handler
from doeff.cesk.handlers.core_handler import core_handler
from doeff.cesk.handlers.graph_handler import graph_handler
from doeff.cesk.handlers.python_async_syntax_escape_handler import python_async_syntax_escape_handler
from doeff.cesk.handlers.scheduler_state_handler import (
    CURRENT_TASK_KEY,
    EXTERNAL_COMPLETION_QUEUE_KEY,
    SPAWN_ASYNC_HANDLER_KEY,
    TASK_QUEUE_KEY,
    scheduler_state_handler,
)
from doeff.cesk.handlers.state_handler import state_handler
from doeff.cesk.handlers.sync_await_handler import sync_await_handler
from doeff.cesk.handlers.sync_external_wait_handler import sync_external_wait_handler
from doeff.cesk.handlers.task_scheduler_handler import task_scheduler_handler
from doeff.cesk.handlers.writer_handler import writer_handler
from doeff.cesk.result import Done, Failed, PythonAsyncSyntaxEscape
from doeff.cesk.runtime_result import (
    EffectStackTrace,
    KStackTrace,
    PythonStackTrace,
    RuntimeResult,
    RuntimeResultImpl,
    build_stacks_from_captured_traceback,
)
from doeff.cesk.state import CESKState, Error, ProgramControl
from doeff.cesk.step import step
from doeff.program import Program

if TYPE_CHECKING:
    from doeff.cesk.types import Environment, Store

T = TypeVar("T")


# =============================================================================
# Handler Presets
# =============================================================================

sync_handlers_preset: list[Handler] = [
    cast(Handler, scheduler_state_handler),
    cast(Handler, sync_external_wait_handler),
    cast(Handler, task_scheduler_handler),
    cast(Handler, sync_await_handler),
    cast(Handler, state_handler),
    cast(Handler, writer_handler),
    cast(Handler, cache_handler),
    cast(Handler, graph_handler),
    cast(Handler, atomic_handler),
    cast(Handler, core_handler),
]

async_handlers_preset: list[Handler] = [
    cast(Handler, scheduler_state_handler),
    cast(Handler, async_external_wait_handler),
    cast(Handler, task_scheduler_handler),
    cast(Handler, python_async_syntax_escape_handler),
    cast(Handler, state_handler),
    cast(Handler, writer_handler),
    cast(Handler, cache_handler),
    cast(Handler, graph_handler),
    cast(Handler, atomic_handler),
    cast(Handler, core_handler),
]


# =============================================================================
# Run Functions
# =============================================================================

def sync_run(
    program: Program[T],
    handlers: list[Handler],
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> RuntimeResult[T]:
    """Run a program synchronously with the given handlers.

    Per SPEC-CESK-EFFECT-BOUNDARIES.md: sync_run should NEVER see
    PythonAsyncSyntaxEscape. Use handlers that handle Await directly
    (e.g., sync_await_handler which runs in a background thread).

    Args:
        program: The program to run.
        handlers: List of handlers, from outermost to innermost.
        env: Optional initial environment.
        store: Optional initial store.

    Returns:
        RuntimeResult containing the final value or error.

    Example:
        result = sync_run(my_program(), sync_handlers_preset)
        print(result.value)
    """
    frozen_env: Environment = FrozenDict(env) if env else FrozenDict()
    final_store: Store = dict(store) if store else {}

    # Set the async handler for spawned tasks (sync_run uses sync_await_handler)
    final_store[SPAWN_ASYNC_HANDLER_KEY] = sync_await_handler

    # Initialize the external completion queue ONCE at the start.
    # This ensures all stores share the same queue reference.
    if EXTERNAL_COMPLETION_QUEUE_KEY not in final_store:
        final_store[EXTERNAL_COMPLETION_QUEUE_KEY] = queue.Queue()

    wrapped = _wrap_with_handlers(program, handlers)

    state = CESKState(
        C=ProgramControl(wrapped),
        E=frozen_env,
        S=final_store,
        K=[],
    )

    try:
        value, final_state = _sync_run_until_done(state)
        return _build_success_result(value, final_state, final_state.S)
    except _ExecutionError as err:
        if isinstance(err.exception, (KeyboardInterrupt, SystemExit, UnhandledEffectError)):
            raise err.exception from None
        return _build_error_result(
            err.exception,
            err.final_state,
            captured_traceback=err.captured_traceback,
        )


async def async_run(
    program: Program[T],
    handlers: list[Handler],
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> RuntimeResult[T]:
    """Run a program asynchronously with the given handlers.

    Handles PythonAsyncSyntaxEscape by awaiting in the caller's event loop.
    Use python_async_syntax_escape_handler to produce escapes for async effects.

    Args:
        program: The program to run.
        handlers: List of handlers, from outermost to innermost.
        env: Optional initial environment.
        store: Optional initial store.

    Returns:
        RuntimeResult containing the final value or error.

    Example:
        result = await async_run(my_program(), async_handlers_preset)
        print(result.value)
    """
    frozen_env: Environment = FrozenDict(env) if env else FrozenDict()
    final_store: Store = dict(store) if store else {}

    # Set the async handler for spawned tasks (async_run uses python_async_syntax_escape_handler)
    final_store[SPAWN_ASYNC_HANDLER_KEY] = python_async_syntax_escape_handler

    # Initialize the external completion queue ONCE at the start.
    # This ensures all stores share the same queue reference.
    # The queue is thread-safe and can receive completions from asyncio tasks.
    if EXTERNAL_COMPLETION_QUEUE_KEY not in final_store:
        final_store[EXTERNAL_COMPLETION_QUEUE_KEY] = queue.Queue()

    wrapped = _wrap_with_handlers(program, handlers)

    state = CESKState(
        C=ProgramControl(wrapped),
        E=frozen_env,
        S=final_store,
        K=[],
    )

    try:
        value, final_state = await _async_run_until_done(state)
        return _build_success_result(value, final_state, final_state.S)
    except asyncio.CancelledError:
        raise
    except _ExecutionError as err:
        if isinstance(err.exception, (KeyboardInterrupt, SystemExit, UnhandledEffectError)):
            raise err.exception from None
        return _build_error_result(
            err.exception,
            err.final_state,
            captured_traceback=err.captured_traceback,
        )
    except Exception as exc:
        return _build_error_result(exc, state)


# =============================================================================
# Internal Helpers
# =============================================================================

def _wrap_with_handlers(program: Program[T], handlers: list[Handler]) -> Program[T]:
    """Wrap a program with the handler stack.

    Handlers are applied so that first in list is outermost (sees effects last).
    [h0, h1, h2] -> h2 sees effects first, h0 sees last.
    """
    result: Program[T] = program
    for handler in reversed(handlers):
        result = WithHandler(
            handler=cast(Handler, handler),
            program=result,
        )
    return result


def _sync_run_until_done(state: CESKState) -> tuple[Any, CESKState]:
    """Step until Done or Failed.

    sync_run only expects Done, Failed, or CESKState from step().
    PythonAsyncSyntaxEscape should NEVER reach here.
    """
    while True:
        result = step(state)

        if isinstance(result, Done):
            return (result.value, state)

        if isinstance(result, Failed):
            raise _ExecutionError(
                exception=result.exception,
                final_state=state,
                captured_traceback=result.captured_traceback,
            )

        if isinstance(result, CESKState):
            state = result
            continue

        if isinstance(result, PythonAsyncSyntaxEscape):
            raise TypeError(
                "sync_run received PythonAsyncSyntaxEscape, which requires async/await. "
                "This typically means python_async_syntax_escape_handler is in your handler stack, "
                "but it is only compatible with async_run.\n\n"
                "To fix this, either:\n"
                "  1. Use async_run instead of sync_run\n"
                "  2. Use sync_handlers_preset (which includes sync_await_handler)\n"
                "  3. Replace python_async_syntax_escape_handler with sync_await_handler"
            )

        raise RuntimeError(
            f"Unexpected step result: {type(result).__name__}. "
            f"sync_run only handles Done, Failed, and CESKState."
        )


async def _async_run_until_done(state: CESKState) -> tuple[Any, CESKState]:
    """Step until Done or Failed, handling PythonAsyncSyntaxEscape via action execution.

    Per SPEC-CESK-005:
    - PythonAsyncSyntaxEscape.action returns CESKState (step() wraps handler's action)
    - async_run just awaits the action and uses the returned state
    - All coordination is handled by the scheduler via ExternalPromise + Wait
    """
    import os
    debug = os.environ.get("DOEFF_ASYNC_DEBUG", "").lower() in ("1", "true", "yes")
    step_count = 0
    while True:
        step_count += 1
        result = step(state)
        if debug and step_count % 100 == 0:
            print(f"[async_run] step {step_count}: result type = {type(result).__name__}")

        if isinstance(result, Done):
            return (result.value, state)

        if isinstance(result, Failed):
            raise _ExecutionError(
                exception=result.exception,
                final_state=state,
                captured_traceback=result.captured_traceback,
            )

        if isinstance(result, PythonAsyncSyntaxEscape):
            # Action returns CESKState directly (step() wraps handler's value-returning action)
            # This is the simple, clean interface per SPEC-CESK-005
            if debug:
                print(f"[async_run] step {step_count}: awaiting PythonAsyncSyntaxEscape action")
            state = await result.action()
            if debug:
                print(f"[async_run] step {step_count}: escape action completed")

            # Yield to event loop to let asyncio tasks progress
            await asyncio.sleep(0)
            continue

        if isinstance(result, CESKState):
            state = result
            # Yield to event loop to let background tasks progress
            await asyncio.sleep(0)
            continue

        raise RuntimeError(f"Unexpected step result: {type(result)}")


class _ExecutionError(Exception):
    """Internal exception for carrying execution errors with state."""

    def __init__(
        self,
        exception: BaseException,
        final_state: CESKState,
        captured_traceback: Any = None,
    ):
        self.exception = exception
        self.final_state = final_state
        self.captured_traceback = captured_traceback
        super().__init__(str(exception))


def _build_success_result(
    value: T,
    state: CESKState,
    final_store: dict[str, Any] | None = None,
) -> RuntimeResultImpl[T]:
    store = final_store if final_store is not None else state.S

    return RuntimeResultImpl(
        _result=Ok(value),
        _raw_store=dict(store),
        _k_stack=KStackTrace(frames=()),
        _effect_stack=EffectStackTrace(),
        _python_stack=PythonStackTrace(frames=()),
    )


def _build_error_result(
    exc: BaseException,
    state: CESKState,
    final_store: dict[str, Any] | None = None,
    captured_traceback: Any = None,
) -> RuntimeResultImpl[Any]:
    store = final_store if final_store is not None else state.S

    if captured_traceback is None:
        captured_traceback = getattr(exc, "__cesk_traceback__", None)
    python_stack, effect_stack = build_stacks_from_captured_traceback(captured_traceback)

    return RuntimeResultImpl(
        _result=Err(exc),  # type: ignore[arg-type]
        _raw_store=dict(store),
        _k_stack=KStackTrace(frames=()),
        _effect_stack=effect_stack,
        _python_stack=python_stack,
        _captured_traceback=captured_traceback,
    )


__all__ = [
    "async_handlers_preset",
    "async_run",
    "sync_handlers_preset",
    "sync_run",
]
