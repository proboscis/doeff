"""CESK machine run loop - deprecated, use doeff.runtimes instead."""

from __future__ import annotations

import asyncio
import logging
import warnings
from typing import TYPE_CHECKING, Any, TypeVar

from doeff._vendor import Err, FrozenDict, Ok, Result
from doeff.cesk.result import CESKResult, Done, Failed, Suspended
from doeff.cesk.state import CESKState, Error
from doeff.cesk.step import step
from doeff.cesk.dispatcher import InterpreterInvariantError, ScheduledEffectDispatcher
from doeff.runtime import (
    AwaitPayload,
    Continuation,
    DelayPayload,
    Resume,
    Schedule,
    SpawnPayload,
    WaitUntilPayload,
)
from doeff.scheduled_handlers import default_scheduled_handlers

if TYPE_CHECKING:
    from doeff.cesk.types import Environment, Store
    from doeff.cesk_observability import OnStepCallback
    from doeff.cesk_traceback import CapturedTraceback
    from doeff.program import Program
    from doeff.runtime import ScheduledHandlers
    from doeff.storage import DurableStorage

T = TypeVar("T")


async def _run_internal(
    program: "Program",
    env: "Environment",
    store: "Store",
    on_step: "OnStepCallback | None" = None,
    storage: "DurableStorage | None" = None,
    dispatcher: "ScheduledEffectDispatcher | None" = None,
) -> tuple[Result[T], "Store", "CapturedTraceback | None"]:
    """Internal run function for executing programs.
    
    This is used internally by concurrency handlers that need access
    to the final store state. For external use, prefer the runtimes
    from doeff.runtimes (AsyncioRuntime, SyncRuntime, SimulationRuntime).
    """
    from doeff.cesk_observability import ExecutionSnapshot
    from doeff.cesk_traceback import capture_traceback_safe

    if dispatcher is None:
        dispatcher = ScheduledEffectDispatcher(builtin_handlers=default_scheduled_handlers())

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
            except Exception as exc:
                logging.warning("on_step callback error: %s", exc)

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
                    payload = handler_result.payload
                    new_store = handler_result.store
                    
                    match payload:
                        case AwaitPayload(awaitable=aw):
                            try:
                                async_result = await aw
                                if isinstance(async_result, tuple) and len(async_result) == 2:
                                    value, result_store = async_result
                                else:
                                    value, result_store = async_result, new_store
                                state = result.resume(value, result_store)
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
                        
                        case DelayPayload(duration=d):
                            await asyncio.sleep(d.total_seconds())
                            state = result.resume(None, new_store)
                        
                        case WaitUntilPayload(target=t):
                            from datetime import datetime
                            now = datetime.now(tz=t.tzinfo)
                            delay = max(0.0, (t - now).total_seconds())
                            await asyncio.sleep(delay)
                            state = result.resume(None, new_store)
                        
                        case SpawnPayload(program=prog, env=e, store=s):
                            child_k = Continuation.from_program(prog, e, s)
                            asyncio.create_task(_run_spawned(child_k, s, dispatcher))
                            state = result.resume(None, new_store)
                        
                        case _:
                            raise InterpreterInvariantError(f"Unknown payload: {type(payload)}")
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


async def _run_spawned(k: Continuation, store: "Store", dispatcher: ScheduledEffectDispatcher) -> None:
    """Run a spawned continuation to completion (fire-and-forget)."""
    state = k.resume(None, store)
    while True:
        result = step(state, dispatcher)
        if isinstance(result, (Done, Failed)):
            return
        if isinstance(result, Suspended):
            handler_result = dispatcher.dispatch(result.effect, state.E, state.S)
            if isinstance(handler_result, Resume):
                state = result.resume(handler_result.value, handler_result.store)
            elif isinstance(handler_result, Schedule):
                payload = handler_result.payload
                if isinstance(payload, AwaitPayload):
                    try:
                        async_result = await payload.awaitable
                        if isinstance(async_result, tuple) and len(async_result) == 2:
                            value, result_store = async_result
                        else:
                            value, result_store = async_result, handler_result.store
                        state = result.resume(value, result_store)
                    except Exception as ex:
                        state = result.resume_error(ex)
                else:
                    state = result.resume(None, handler_result.store)
            continue
        if isinstance(result, CESKState):
            state = result
            continue
        return


async def run(
    program: "Program[T]",
    env: "Environment | dict[Any, Any] | None" = None,
    store: "Store | None" = None,
    *,
    storage: "DurableStorage | None" = None,
    on_step: "OnStepCallback | None" = None,
    scheduled_handlers: "ScheduledHandlers | None" = None,
) -> CESKResult[T]:
    """Run a program asynchronously.
    
    Deprecated: Use AsyncioRuntime().run() from doeff.runtimes instead.
    """
    warnings.warn(
        "doeff.cesk.run() is deprecated. Use AsyncioRuntime().run() from doeff.runtimes instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    if env is None:
        E: "Environment" = FrozenDict()
    elif isinstance(env, FrozenDict):
        E = env
    else:
        E = FrozenDict(env)

    S: "Store" = store if store is not None else {}
    if storage is not None:
        S = {**S, "__durable_storage__": storage}

    dispatcher = ScheduledEffectDispatcher(
        user_handlers=scheduled_handlers,
        builtin_handlers=default_scheduled_handlers(),
    )

    result, _, captured_traceback = await _run_internal(
        program, E, S, on_step=on_step, storage=storage, dispatcher=dispatcher
    )
    return CESKResult(result, captured_traceback)


def run_sync(
    program: "Program[T]",
    env: "Environment | dict[Any, Any] | None" = None,
    store: "Store | None" = None,
    *,
    storage: "DurableStorage | None" = None,
    on_step: "OnStepCallback | None" = None,
    scheduled_handlers: "ScheduledHandlers | None" = None,
) -> CESKResult[T]:
    """Run a program synchronously.
    
    Deprecated: Use SyncRuntime().run() from doeff.runtimes instead.
    """
    warnings.warn(
        "doeff.cesk.run_sync() is deprecated. Use SyncRuntime().run() from doeff.runtimes instead.",
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
        )
    )


__all__ = [
    "_run_internal",
    "run",
    "run_sync",
]
