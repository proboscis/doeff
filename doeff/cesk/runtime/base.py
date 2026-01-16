"""BaseRuntime abstract class for the unified CESK architecture."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, TypeVar

from doeff._vendor import FrozenDict, Ok, Err
from doeff.cesk.state import (
    CESKState,
    TaskState,
    Done as TaskDone,
    Value,
    Error,
    EffectControl,
    ProgramControl,
    Ready,
)
from doeff.cesk.result import Done, Failed
from doeff.cesk.step import step
from doeff.cesk.handlers import Handler, default_handlers

if TYPE_CHECKING:
    from doeff.program import Program
    from doeff.cesk.types import Environment, Store

T = TypeVar("T")


class BaseRuntime(ABC):
    def __init__(self, handlers: dict[type, Handler] | None = None):
        self._handlers = handlers if handlers is not None else default_handlers()

    @abstractmethod
    def run(
        self,
        program: Program,
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> Any:
        pass

    def _create_initial_state(
        self,
        program: Program,
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> CESKState:
        frozen_env: Environment
        if env is None:
            frozen_env = FrozenDict()
        else:
            frozen_env = FrozenDict(env)
        
        final_store: Store = store if store is not None else {}
        return CESKState.initial(program, frozen_env, final_store)

    def _step_until_done(self, state: CESKState) -> Any:
        from doeff.cesk.dispatcher import ScheduledEffectDispatcher
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
            
            from doeff.cesk.result import Suspended
            if isinstance(result, Suspended):
                handler_result = dispatcher.dispatch(result.effect, state.E, state.S)
                from doeff.runtime import Resume, Schedule
                if isinstance(handler_result, Resume):
                    state = result.resume(handler_result.value, handler_result.store)
                elif isinstance(handler_result, Schedule):
                    state = self._handle_schedule(result, handler_result, state)
                continue
            
            raise RuntimeError(f"Unexpected step result: {type(result)}")

    def _handle_schedule(
        self,
        suspended: Any,
        schedule_result: Any,
        state: CESKState,
    ) -> CESKState:
        return suspended.resume(None, schedule_result.store)


__all__ = [
    "BaseRuntime",
]
