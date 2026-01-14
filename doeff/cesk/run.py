"""CESK machine run loop and public API functions."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, TypeVar

from doeff._vendor import Err, FrozenDict, Ok, Result
from doeff.cesk.types import Environment, Store
from doeff.cesk.state import CESKState, Error
from doeff.cesk.result import CESKResult, Done, Failed, Suspended
from doeff.cesk.step import step
from doeff.cesk.dispatcher import InterpreterInvariantError, ScheduledEffectDispatcher
from doeff.runtime import (
    Continuation,
    Pending,
    Ready,
    Resume,
    Schedule,
    Scheduled,
    Scheduler,
    Suspend,
)
from doeff.scheduled_handlers import default_scheduled_handlers

if TYPE_CHECKING:
    from doeff.cesk_observability import OnStepCallback
    from doeff.cesk_traceback import CapturedTraceback
    from doeff.program import Program
    from doeff.runtime import ScheduledHandlers
    from doeff.storage import DurableStorage

T = TypeVar("T")


async def _run_internal(
    program: Program,
    env: Environment,
    store: Store,
    on_step: OnStepCallback | None = None,
    storage: DurableStorage | None = None,
    dispatcher: ScheduledEffectDispatcher | None = None,
    scheduler: Scheduler | None = None,
) -> tuple[Result[T], Store, CapturedTraceback | None]:
    from doeff.cesk_observability import ExecutionSnapshot
    from doeff.cesk_traceback import capture_traceback_safe
    from doeff.runtime import FIFOScheduler

    if dispatcher is None:
        dispatcher = ScheduledEffectDispatcher(builtin_handlers=default_scheduled_handlers())

    if scheduler is None:
        scheduler = FIFOScheduler()

    store = {**store, "__dispatcher__": dispatcher}

    state = CESKState.initial(program, env, store)
    step_count = 0

    while True:
        result = step(state, dispatcher)
        step_count += 1

        if on_step is not None:
            try:
                if isinstance(result, Done):
                    snapshot = ExecutionSnapshot.from_state(state, "completed", step_count, storage)
                elif isinstance(result, Failed):
                    snapshot = ExecutionSnapshot.from_state(state, "failed", step_count, storage)
                elif isinstance(result, Suspended):
                    snapshot = ExecutionSnapshot.from_state(state, "paused", step_count, storage)
                else:
                    snapshot = ExecutionSnapshot.from_state(result, "running", step_count, storage)
                on_step(snapshot)
            except Exception as e:
                import logging
                logging.warning(f"on_step callback error (ignored): {e}", exc_info=True)

        if isinstance(result, Done):
            return Ok(result.value), result.store, None

        if isinstance(result, Failed):
            return Err(result.exception), result.store, result.captured_traceback

        if isinstance(result, Suspended):
            effect = result.effect
            original_store = state.S

            try:
                handler_result = dispatcher.dispatch(effect, state.E, original_store)

                if isinstance(handler_result, Resume):
                    state = result.resume(handler_result.value, handler_result.store)
                elif isinstance(handler_result, Schedule):
                    try:
                        k = Continuation(
                            _resume=result.resume,
                            _resume_error=result.resume_error,
                            env=state.E,
                            store=handler_result.store,
                        )
                        scheduler.submit(k, handler_result.payload, handler_result.store)
                        item = scheduler.next()
                        if item is None:
                            raise InterpreterInvariantError("Schedule but no continuation in scheduler")

                        match item.result:
                            case Ready(value):
                                state = item.k.resume(value, item.store)
                            case Pending(awaitable):
                                value, new_store = await awaitable
                                state = item.k.resume(value, new_store)
                            case _:
                                raise InterpreterInvariantError(f"Unknown scheduler result: {type(item.result)}")
                    except Exception as ex:
                        captured = capture_traceback_safe(state.K, ex)
                        error_state = result.resume_error(ex)
                        if isinstance(error_state.C, Error) and error_state.C.captured_traceback is None:
                            error_state = CESKState(
                                C=Error(ex, captured_traceback=captured),
                                E=error_state.E,
                                S=error_state.S,
                                K=error_state.K,
                            )
                        state = error_state
                elif isinstance(handler_result, Suspend):
                    try:
                        async_result = await handler_result.awaitable
                        if isinstance(async_result, tuple) and len(async_result) == 2:
                            value, new_store = async_result
                        else:
                            value, new_store = async_result, handler_result.store
                        state = result.resume(value, new_store)
                    except Exception as ex:
                        captured = capture_traceback_safe(state.K, ex)
                        error_state = result.resume_error(ex)
                        if isinstance(error_state.C, Error) and error_state.C.captured_traceback is None:
                            error_state = CESKState(
                                C=Error(ex, captured_traceback=captured),
                                E=error_state.E,
                                S=error_state.S,
                                K=error_state.K,
                            )
                        state = error_state
                elif isinstance(handler_result, Scheduled):
                    item = scheduler.next()
                    if item is not None:
                        match item.result:
                            case Ready(value):
                                state = item.k.resume(value, item.store)
                            case Pending(awaitable):
                                value, new_store = await awaitable
                                state = item.k.resume(value, new_store)
                            case _:
                                raise InterpreterInvariantError(f"Unknown scheduler result: {type(item.result)}")
                    else:
                        raise InterpreterInvariantError("Scheduled but no continuation in scheduler")
                else:
                    raise InterpreterInvariantError(f"Unknown handler result: {type(handler_result)}")
            except Exception as ex:
                from doeff.cesk.dispatcher import UnhandledEffectError
                if isinstance(ex, UnhandledEffectError):
                    raise
                captured = capture_traceback_safe(state.K, ex)
                error_state = result.resume_error(ex)
                if isinstance(error_state.C, Error) and error_state.C.captured_traceback is None:
                    error_state = CESKState(
                        C=Error(ex, captured_traceback=captured),
                        E=error_state.E,
                        S=error_state.S,
                        K=error_state.K,
                    )
                state = error_state
            continue

        if isinstance(result, CESKState):
            state = result
            continue

        raise InterpreterInvariantError(f"Unexpected step result: {type(result).__name__}")


async def run(
    program: Program,
    env: Environment | dict[Any, Any] | None = None,
    store: Store | None = None,
    *,
    storage: DurableStorage | None = None,
    on_step: OnStepCallback | None = None,
    scheduled_handlers: ScheduledHandlers | None = None,
    scheduler: Scheduler | None = None,
) -> CESKResult[T]:
    import warnings
    warnings.warn(
        "run() is deprecated. Use EffectRuntime(scheduler).run() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    if env is None:
        E = FrozenDict()
    elif isinstance(env, FrozenDict):
        E = env
    else:
        E = FrozenDict(env)

    S = store if store is not None else {}
    if storage is not None:
        S = {**S, "__durable_storage__": storage}

    dispatcher = ScheduledEffectDispatcher(
        user_handlers=scheduled_handlers,
        builtin_handlers=default_scheduled_handlers(),
    )

    result, _, captured_traceback = await _run_internal(
        program, E, S, on_step=on_step, storage=storage, dispatcher=dispatcher, scheduler=scheduler
    )
    return CESKResult(result, captured_traceback)


def run_sync(
    program: Program,
    env: Environment | dict[Any, Any] | None = None,
    store: Store | None = None,
    *,
    storage: DurableStorage | None = None,
    on_step: OnStepCallback | None = None,
    scheduled_handlers: ScheduledHandlers | None = None,
    scheduler: Scheduler | None = None,
) -> CESKResult[T]:
    import warnings
    warnings.warn(
        "run_sync() is deprecated. Use EffectRuntime(scheduler).run_sync() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return asyncio.run(
        run(
            program,
            env,
            store,
            storage=storage,
            on_step=on_step,
            scheduled_handlers=scheduled_handlers,
            scheduler=scheduler,
        )
    )


__all__ = [
    "_run_internal",
    "run",
    "run_sync",
]
