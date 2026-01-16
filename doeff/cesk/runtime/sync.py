"""Synchronous runtime for the unified CESK architecture."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from doeff._vendor import FrozenDict
from doeff.cesk.events import (
    AllTasksComplete,
    EffectSuspended,
    Stepped,
    TaskCompleted,
    TaskFailed,
)
from doeff.cesk.handlers import HandlerRegistry, default_handlers
from doeff.cesk.unified_state import UnifiedCESKState as CESKState
from doeff.cesk.unified_step import unified_step

if TYPE_CHECKING:
    from doeff.cesk.types import Environment, Store
    from doeff.program import Program

T = TypeVar("T")


class SyncRuntimeError(Exception):
    def __init__(self, message: str, cause: BaseException | None = None):
        super().__init__(message)
        self.cause = cause


class UnifiedSyncRuntime:
    def __init__(self, handlers: HandlerRegistry | None = None):
        self._handlers = handlers or default_handlers()
    
    def run(
        self,
        program: Program[T],
        env: Environment | dict[Any, Any] | None = None,
        store: Store | None = None,
    ) -> T:
        if env is None:
            env_frozen: Environment = FrozenDict()
        elif isinstance(env, FrozenDict):
            env_frozen = env
        else:
            env_frozen = FrozenDict(env)
        
        state = CESKState.initial(program, env_frozen, store)
        max_steps = 100_000
        
        for _ in range(max_steps):
            event = unified_step(state, self._handlers)
            
            if isinstance(event, TaskCompleted):
                return event.value
            
            if isinstance(event, TaskFailed):
                raise SyncRuntimeError(str(event.error), event.error)
            
            if isinstance(event, AllTasksComplete):
                result = event.state.main_result()
                if result is not None:
                    value, is_success = result
                    if is_success:
                        return value
                    raise SyncRuntimeError(str(value), value if isinstance(value, BaseException) else None)
                raise SyncRuntimeError("Program completed without result")
            
            if isinstance(event, EffectSuspended):
                raise SyncRuntimeError(
                    f"Unhandled effect: {type(event.effect).__name__}"
                )
            
            if isinstance(event, Stepped):
                state = event.state
                continue
            
            raise SyncRuntimeError(f"Unknown event type: {type(event)}")
        
        raise SyncRuntimeError("Maximum steps exceeded")


__all__ = [
    "UnifiedSyncRuntime",
    "SyncRuntimeError",
]
