from __future__ import annotations

import asyncio
import inspect
from datetime import datetime
from typing import TYPE_CHECKING, Any

from doeff._vendor import FrozenDict
from doeff.cesk.runtime.base import BaseRuntime
from doeff.cesk.state import CESKState
from doeff.cesk.result import Done, Failed, Suspended
from doeff.cesk.step import step
from doeff.cesk.handlers import Handler
from doeff.cesk.frames import ContinueValue, ContinueError
from doeff.cesk.errors import UnhandledEffectError
from doeff.effects.future import FutureAwaitEffect
from doeff.effects.time import DelayEffect

if TYPE_CHECKING:
    from doeff.program import Program


class AsyncRuntime(BaseRuntime):
    def __init__(self, handlers: dict[type, Handler] | None = None):
        super().__init__(handlers)

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

                if isinstance(effect, FutureAwaitEffect):
                    try:
                        awaitable = effect.awaitable
                        value = await awaitable
                        state = result.resume(value, state.store)
                    except Exception as ex:
                        state = result.resume_error(ex)
                    continue

                if isinstance(effect, DelayEffect):
                    await asyncio.sleep(effect.seconds)
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
