"""SimulationRuntime with deterministic time control."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from doeff._vendor import FrozenDict
from doeff.cesk.runtime.base import BaseRuntime
from doeff.cesk.state import CESKState
from doeff.cesk.result import Done, Failed
from doeff.cesk.step import step
from doeff.cesk.handlers import Handler

if TYPE_CHECKING:
    from doeff.program import Program


class SimulationRuntime(BaseRuntime):
    def __init__(
        self,
        handlers: dict[type, Handler] | None = None,
        start_time: datetime | None = None,
    ):
        super().__init__(handlers)
        self._current_time = start_time if start_time is not None else datetime.now()

    @property
    def current_time(self) -> datetime:
        return self._current_time

    def advance_time(self, delta: timedelta) -> None:
        self._current_time = self._current_time + delta

    def set_time(self, time: datetime) -> None:
        self._current_time = time

    def run(
        self,
        program: Program,
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> Any:
        initial_store = store if store is not None else {}
        initial_store = {**initial_store, "__current_time__": self._current_time}
        
        state = self._create_initial_state(program, env, initial_store)
        return self._step_until_done_simulation(state)

    def _step_until_done_simulation(self, state: CESKState) -> Any:
        from doeff.cesk.dispatcher import ScheduledEffectDispatcher
        from doeff.cesk.result import Suspended
        from doeff.runtime import Resume, Schedule, DelayPayload, WaitUntilPayload
        from doeff.scheduled_handlers import default_scheduled_handlers
        
        dispatcher = ScheduledEffectDispatcher(builtin_handlers=default_scheduled_handlers())
        
        while True:
            result = step(state, dispatcher)
            
            if isinstance(result, Done):
                return result.value
            
            if isinstance(result, Failed):
                raise result.exception
            
            if isinstance(result, CESKState):
                state = result
                continue
            
            if isinstance(result, Suspended):
                handler_result = dispatcher.dispatch(result.effect, state.E, state.S)
                
                if isinstance(handler_result, Resume):
                    state = result.resume(handler_result.value, handler_result.store)
                elif isinstance(handler_result, Schedule):
                    payload = handler_result.payload
                    new_store = handler_result.store
                    
                    if isinstance(payload, DelayPayload):
                        self._current_time = self._current_time + payload.duration
                        new_store = {**new_store, "__current_time__": self._current_time}
                        state = result.resume(None, new_store)
                    elif isinstance(payload, WaitUntilPayload):
                        if payload.target > self._current_time:
                            self._current_time = payload.target
                        new_store = {**new_store, "__current_time__": self._current_time}
                        state = result.resume(None, new_store)
                    else:
                        state = result.resume(None, new_store)
                continue
            
            raise RuntimeError(f"Unexpected step result: {type(result)}")


__all__ = [
    "SimulationRuntime",
]
