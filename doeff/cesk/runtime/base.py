from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, TypeVar

from doeff._vendor import FrozenDict
from doeff.cesk.state import CESKState, TaskState, Ready
from doeff.cesk.result import Done, Failed, Suspended
from doeff.cesk.step import step
from doeff.cesk.handlers import Handler, default_handlers
from doeff.cesk.frames import ContinueValue, ContinueError
from doeff.cesk.errors import UnhandledEffectError

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

    def _dispatch_effect(
        self,
        effect: Any,
        task_state: TaskState,
        store: dict[str, Any],
    ) -> ContinueValue | ContinueError:
        handler = self._handlers.get(type(effect))
        
        if handler is None:
            return ContinueError(
                error=UnhandledEffectError(f"No handler for {type(effect).__name__}"),
                env=task_state.env,
                store=store,
                k=task_state.kontinuation,
            )
        
        try:
            frame_result = handler(effect, task_state, store)
        except Exception as ex:
            return ContinueError(error=ex, env=task_state.env, store=store, k=task_state.kontinuation)
        
        if isinstance(frame_result, (ContinueValue, ContinueError)):
            return frame_result
        
        return ContinueError(
            error=RuntimeError(f"Handler returned unexpected type: {type(frame_result)}"),
            env=task_state.env,
            store=store,
            k=task_state.kontinuation,
        )

    def _step_until_done(self, state: CESKState) -> Any:
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
                main_task = state.tasks[state.main_task]
                dispatch_result = self._dispatch_effect(
                    result.effect, main_task, state.store
                )
                
                if isinstance(dispatch_result, ContinueError):
                    state = result.resume_error(dispatch_result.error)
                else:
                    state = result.resume(dispatch_result.value, dispatch_result.store)
                continue
            
            raise RuntimeError(f"Unexpected step result: {type(result)}")


__all__ = [
    "BaseRuntime",
]
