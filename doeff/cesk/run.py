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
from typing import TYPE_CHECKING, Any, TypeVar, cast

from doeff._vendor import Err, FrozenDict, Ok
from doeff.cesk.errors import UnhandledEffectError
from doeff.cesk.handler_frame import Handler, WithHandler
from doeff.cesk.handlers.core_handler import core_handler
from doeff.cesk.handlers.python_async_syntax_escape_handler import python_async_syntax_escape_handler
from doeff.cesk.handlers.scheduler_state_handler import (
    CURRENT_TASK_KEY,
    EXTERNAL_COMPLETION_QUEUE_KEY,
    EXTERNAL_PROMISE_REGISTRY_KEY,
    TASK_QUEUE_KEY,
    process_external_completions,
    scheduler_state_handler,
)
from doeff.cesk.handlers.sync_await_handler import sync_await_handler
from doeff.cesk.handlers.task_scheduler_handler import task_scheduler_handler
from doeff.cesk.result import Done, Failed, PythonAsyncSyntaxEscape, WaitingForExternalCompletion
from doeff.cesk.runtime_result import (
    EffectStackTrace,
    KStackTrace,
    PythonStackTrace,
    RuntimeResult,
    RuntimeResultImpl,
    build_stacks_from_captured_traceback,
)
from doeff.cesk.state import CESKState, Error, ProgramControl, Value
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
    cast(Handler, task_scheduler_handler),
    cast(Handler, sync_await_handler),
    cast(Handler, core_handler),
]
"""Handler preset for sync_run.

Includes (outermost to innermost):
- scheduler_state_handler: Task queue management
- task_scheduler_handler: Spawn/Wait/Gather/Race
- sync_await_handler: Async effects via background thread
- core_handler: Get/Put/Ask/etc.
"""

async_handlers_preset: list[Handler] = [
    cast(Handler, scheduler_state_handler),
    cast(Handler, task_scheduler_handler),
    cast(Handler, python_async_syntax_escape_handler),
    cast(Handler, core_handler),
]
"""Handler preset for async_run.

Includes (outermost to innermost):
- scheduler_state_handler: Task queue management
- task_scheduler_handler: Spawn/Wait/Gather/Race
- python_async_syntax_escape_handler: Produces PythonAsyncSyntaxEscape for await
- core_handler: Get/Put/Ask/etc.
"""


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
        # Process any pending external promise completions
        process_external_completions(state.S)

        result = step(state)

        if isinstance(result, Done):
            return (result.value, state)

        if isinstance(result, Failed):
            # Check if this is a deadlock that might be resolved by external completion
            is_deadlock = (
                isinstance(result.exception, RuntimeError)
                and "Deadlock" in str(result.exception)
            )
            if is_deadlock:
                # Check if there are pending external promises
                external_registry = state.S.get(EXTERNAL_PROMISE_REGISTRY_KEY, {})
                completion_queue = state.S.get(EXTERNAL_COMPLETION_QUEUE_KEY)

                if external_registry and completion_queue is not None:
                    # Wait for external completion (blocking)
                    try:
                        promise_id, value, error = completion_queue.get(timeout=30.0)
                        completion_queue.put((promise_id, value, error))  # Put back for process_external_completions
                        # Process and retry
                        process_external_completions(state.S)
                        continue
                    except Exception:
                        pass  # Timeout or error - fall through to raise

            raise _ExecutionError(
                exception=result.exception,
                final_state=state,
                captured_traceback=result.captured_traceback,
            )

        if isinstance(result, CESKState):
            state = result
            continue

        if isinstance(result, WaitingForExternalCompletion):
            # Block-wait for external completion
            completion_queue = state.S.get(EXTERNAL_COMPLETION_QUEUE_KEY)
            if completion_queue is not None:
                try:
                    # Block until external completion arrives (with timeout)
                    completion = completion_queue.get(timeout=30.0)
                    # Put back for process_external_completions to handle
                    completion_queue.put(completion)
                    # Process completions (this wakes up waiters and adds them to queue)
                    process_external_completions(state.S)

                    # Get the resume value/error from the queue
                    task_queue = state.S.get(TASK_QUEUE_KEY, [])
                    if task_queue:
                        item = task_queue.pop(0)
                        state.S[TASK_QUEUE_KEY] = task_queue

                        resume_value = item.get("resume_value")
                        resume_error = item.get("resume_error")

                        # Resume with the full continuation (from WaitingForExternalCompletion.state)
                        # and the result value/error from the queue
                        if resume_error is not None:
                            state = CESKState(
                                C=Error(resume_error),
                                E=result.state.E,
                                S=state.S,  # Current store with updated scheduler state
                                K=result.state.K,  # Full continuation including handler frames
                            )
                        else:
                            state = CESKState(
                                C=Value(resume_value),
                                E=result.state.E,
                                S=state.S,
                                K=result.state.K,
                            )
                        continue
                except Exception:
                    # Timeout or error - fall through to raise
                    pass

            raise RuntimeError(
                "Deadlock: waiting for external promise but no completion received"
            )

        raise RuntimeError(
            f"Unexpected step result: {type(result).__name__}. "
            f"sync_run only handles Done, Failed, and CESKState. "
            f"For async effects, use handlers that handle Await directly."
        )


async def _async_run_until_done(state: CESKState) -> tuple[Any, CESKState]:
    """Step until Done or Failed, handling PythonAsyncSyntaxEscape via await."""
    from doeff.cesk.result import DirectState

    pending_tasks: dict[Any, asyncio.Task[Any]] = {}

    def _cancel_pending_tasks() -> None:
        """Cancel all pending asyncio tasks to avoid 'coroutine never awaited' warnings."""
        for task in pending_tasks.values():
            if not task.done():
                task.cancel()

    try:
        while True:
            result = step(state)

            if isinstance(result, Done):
                _cancel_pending_tasks()
                return (result.value, state)

            if isinstance(result, Failed):
                _cancel_pending_tasks()
                raise _ExecutionError(
                    exception=result.exception,
                    final_state=state,
                    captured_traceback=result.captured_traceback,
                )

            if isinstance(result, PythonAsyncSyntaxEscape):
                current_store = result.store if result.store is not None else state.S

                if result.awaitables:
                    # Multi-task case: await first completion
                    for task_id, awaitable in result.awaitables.items():
                        if task_id not in pending_tasks:
                            pending_tasks[task_id] = asyncio.create_task(awaitable)

                    done, _ = await asyncio.wait(
                        pending_tasks.values(),
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    for task_id, atask in list(pending_tasks.items()):
                        if atask in done:
                            del pending_tasks[task_id]
                            try:
                                value = atask.result()
                                resume_result = result.resume((task_id, value), current_store)
                                state = resume_result.state if isinstance(resume_result, DirectState) else resume_result
                            except asyncio.CancelledError:
                                raise
                            except Exception as ex:
                                error_result = result.resume_error((task_id, ex))
                                state = error_result.state if isinstance(error_result, DirectState) else error_result
                            break
                    else:
                        _cancel_pending_tasks()
                        raise RuntimeError("asyncio.wait returned but no task completed")
                elif result.awaitable is not None:
                    # Single awaitable case
                    try:
                        value = await result.awaitable
                        resume_result = result.resume(value, current_store)
                        state = resume_result.state if isinstance(resume_result, DirectState) else resume_result
                    except asyncio.CancelledError:
                        raise
                    except Exception as ex:
                        error_result = result.resume_error(ex)
                        state = error_result.state if isinstance(error_result, DirectState) else error_result
                else:
                    _cancel_pending_tasks()
                    raise RuntimeError("PythonAsyncSyntaxEscape with neither awaitable nor awaitables")
                continue

            if isinstance(result, CESKState):
                state = result
                continue

            if isinstance(result, WaitingForExternalCompletion):
                # In async context, poll for external completion with asyncio.sleep
                completion_queue = state.S.get(EXTERNAL_COMPLETION_QUEUE_KEY)
                if completion_queue is not None:
                    # Poll loop with async sleep to not block event loop
                    max_attempts = 3000  # 30 seconds total
                    for _ in range(max_attempts):
                        if not completion_queue.empty():
                            completion = completion_queue.get_nowait()
                            completion_queue.put(completion)
                            process_external_completions(state.S)

                            # Get the resume value/error from the queue
                            task_queue = state.S.get(TASK_QUEUE_KEY, [])
                            if task_queue:
                                item = task_queue.pop(0)
                                state.S[TASK_QUEUE_KEY] = task_queue

                                resume_value = item.get("resume_value")
                                resume_error = item.get("resume_error")

                                # Resume with the full continuation (from WaitingForExternalCompletion.state)
                                # and the result value/error from the queue
                                if resume_error is not None:
                                    state = CESKState(
                                        C=Error(resume_error),
                                        E=result.state.E,
                                        S=state.S,  # Current store with updated scheduler state
                                        K=result.state.K,  # Full continuation including handler frames
                                    )
                                else:
                                    state = CESKState(
                                        C=Value(resume_value),
                                        E=result.state.E,
                                        S=state.S,
                                        K=result.state.K,
                                    )
                                break
                        await asyncio.sleep(0.01)
                    else:
                        _cancel_pending_tasks()
                        raise RuntimeError(
                            "Timeout: waiting for external promise but no completion received"
                        )
                    continue

                _cancel_pending_tasks()
                raise RuntimeError(
                    "Deadlock: waiting for external promise but no completion queue"
                )

            _cancel_pending_tasks()
            raise RuntimeError(f"Unexpected step result: {type(result)}")
    except Exception:
        _cancel_pending_tasks()
        raise


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
