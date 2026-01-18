from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from doeff.cesk.runtime.base import BaseRuntime
from doeff.cesk.state import CESKState, TaskState
from doeff.cesk.result import Done, Failed, Suspended
from doeff.cesk.step import step
from doeff.cesk.handlers import Handler, default_handlers
from doeff.cesk.frames import ContinueValue, ContinueError
from doeff.cesk.types import Store
from doeff.effects.future import FutureAwaitEffect
from doeff.effects.time import DelayEffect, WaitUntilEffect

if TYPE_CHECKING:
    from doeff.program import Program


def _async_delay_placeholder(
    effect: DelayEffect,
    task_state: TaskState,
    store: Store,
) -> ContinueValue:
    return ContinueValue(
        value=None,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


def _async_wait_until_placeholder(
    effect: WaitUntilEffect,
    task_state: TaskState,
    store: Store,
) -> ContinueValue:
    return ContinueValue(
        value=None,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


def _async_await_placeholder(
    effect: FutureAwaitEffect,
    task_state: TaskState,
    store: Store,
) -> ContinueValue:
    return ContinueValue(
        value=None,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


class AsyncRuntime(BaseRuntime):
    _ASYNC_EFFECT_TYPES = (FutureAwaitEffect, DelayEffect, WaitUntilEffect)

    def __init__(self, handlers: dict[type, Handler] | None = None):
        base_handlers = default_handlers()
        base_handlers[DelayEffect] = _async_delay_placeholder
        base_handlers[WaitUntilEffect] = _async_wait_until_placeholder
        base_handlers[FutureAwaitEffect] = _async_await_placeholder
        
        if handlers:
            base_handlers.update(handlers)
        
        super().__init__(base_handlers)
        
        self._user_handlers = handlers or {}

    async def run(
        self,
        program: Program,
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> Any:
        state = self._create_initial_state(program, env, store)
        return await self._step_until_done_async(state)

    async def _step_until_done_async(self, state: CESKState) -> Any:
        while True:
            result = step(state, self._handlers)

            if isinstance(result, Done):
                return result.value

            if isinstance(result, Failed):
                exc = result.exception
                if result.captured_traceback is not None:
                    exc.__cesk_traceback__ = result.captured_traceback  # type: ignore[attr-defined]
                raise exc

            if isinstance(result, CESKState):
                state = result
                continue

            if isinstance(result, Suspended):
                effect = result.effect
                effect_type = type(effect)

                if effect_type in self._user_handlers:
                    main_task = state.tasks[state.main_task]
                    dispatch_result = self._dispatch_effect(
                        effect, main_task, state.store
                    )
                    if isinstance(dispatch_result, ContinueError):
                        state = result.resume_error(dispatch_result.error)
                    else:
                        state = result.resume(dispatch_result.value, dispatch_result.store)
                    continue

                if isinstance(effect, FutureAwaitEffect):
                    try:
                        value = await effect.awaitable
                        state = result.resume(value, state.store)
                    except Exception as ex:
                        state = result.resume_error(ex)
                    continue

                if isinstance(effect, DelayEffect):
                    await asyncio.sleep(effect.seconds)
                    state = result.resume(None, state.store)
                    continue

                if isinstance(effect, WaitUntilEffect):
                    now = datetime.now()
                    if effect.target_time > now:
                        delay_seconds = (effect.target_time - now).total_seconds()
                        await asyncio.sleep(delay_seconds)
                    state = result.resume(None, state.store)
                    continue

                main_task = state.tasks[state.main_task]
                dispatch_result = self._dispatch_effect(
                    effect, main_task, state.store
                )

                if isinstance(dispatch_result, ContinueError):
                    state = result.resume_error(dispatch_result.error)
                else:
                    state = result.resume(dispatch_result.value, dispatch_result.store)
                continue

            raise RuntimeError(f"Unexpected step result: {type(result)}")


__all__ = [
    "AsyncRuntime",
]
