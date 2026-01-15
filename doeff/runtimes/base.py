"""Shared runtime machinery and result types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from doeff._vendor import Err, FrozenDict, Ok, Result
from doeff.cesk.state import CESKState
from doeff.cesk.result import Done, Failed, Suspended
from doeff.cesk.step import step
from doeff.cesk.dispatcher import ScheduledEffectDispatcher, InterpreterInvariantError
from doeff.runtime import (
    Continuation,
    Resume,
    Schedule,
    SchedulePayload,
)
from doeff.scheduled_handlers import default_scheduled_handlers

if TYPE_CHECKING:
    from doeff.cesk.types import Environment, Store
    from doeff.program import Program
    from doeff.runtime import ScheduledHandlers

T = TypeVar("T")


class EffectError(Exception):
    """Exception raised when effect execution fails.
    
    Contains cause exception and effect traceback for debugging.
    """
    
    def __init__(
        self,
        message: str,
        cause: BaseException | None = None,
        effect_traceback: Any = None,
    ):
        super().__init__(message)
        if cause:
            self.__cause__ = cause
        self.cause = cause
        self.effect_traceback = effect_traceback
        self.final_store: "Store | None" = None
        self.final_env: "Environment | None" = None
    
    def format_full(self) -> str:
        """Format full error with effect traceback."""
        parts = [str(self)]
        if self.effect_traceback:
            parts.append(f"\nEffect traceback:\n{self.effect_traceback}")
        if self.cause:
            parts.append(f"\nCaused by: {type(self.cause).__name__}: {self.cause}")
        return "".join(parts)


@dataclass(frozen=True)
class RuntimeResult(Generic[T]):
    """Result from runtime execution. Used by run_safe()."""
    
    result: Result[T]
    effect_traceback: Any = None
    final_store: "Store | None" = None
    final_env: "Environment | None" = None
    
    @property
    def is_ok(self) -> bool:
        return isinstance(self.result, Ok)
    
    @property
    def is_err(self) -> bool:
        return isinstance(self.result, Err)
    
    def unwrap(self) -> T:
        """Get value or raise if error."""
        return self.result.unwrap()
    
    def unwrap_err(self) -> Exception:
        """Get error or raise if ok."""
        return self.result.unwrap_err()
    
    def display(self) -> str:
        if self.is_ok:
            return f"Ok({self.result.ok()!r})"
        parts = [f"Err({self.result.err()!r})"]
        if self.effect_traceback:
            parts.append(f"\nEffect traceback:\n{self.effect_traceback}")
        return "".join(parts)


class RuntimeMixin:
    """Shared CESK machinery for all runtime implementations.
    
    Provides stepping, continuation creation, and payload extraction.
    Does NOT define run() - each runtime defines its own signature.
    """
    
    _handlers: "ScheduledHandlers"
    
    def _init_handlers(self, handlers: "ScheduledHandlers | None" = None) -> None:
        self._handlers = handlers or default_scheduled_handlers()
    
    def _create_dispatcher(self) -> ScheduledEffectDispatcher:
        return ScheduledEffectDispatcher(builtin_handlers=self._handlers)
    
    def _prepare_env_store(
        self,
        env: "Environment | dict | None",
        store: "Store | None",
        dispatcher: ScheduledEffectDispatcher,
    ) -> tuple["Environment", "Store"]:
        E: Environment = FrozenDict(env) if env else FrozenDict()
        S: Store = store or {}
        S = {**S, "__dispatcher__": dispatcher}
        return E, S
    
    def _step_until_effect(
        self,
        state: CESKState,
        dispatcher: ScheduledEffectDispatcher,
    ) -> Done | Failed | tuple[Suspended, CESKState]:
        """Step CESK machine until terminal or suspended state.
        
        Returns:
            Done/Failed for terminal states.
            (Suspended, last_state) tuple when an effect needs scheduling.
        """
        current = state
        
        while True:
            result = step(current, dispatcher)
            
            if isinstance(result, (Done, Failed)):
                return result
            
            if isinstance(result, Suspended):
                effect = result.effect
                handler_result = dispatcher.dispatch(effect, current.E, current.S)
                
                match handler_result:
                    case Resume(value=v, store=s):
                        current = result.resume(v, s)
                    case Schedule():
                        return (result, current)
                    case _:
                        raise InterpreterInvariantError(
                            f"Unknown handler result: {handler_result}"
                        )
            else:
                current = result
    
    def _make_continuation(
        self,
        suspended: Suspended,
        last_state: CESKState,
        new_store: "Store",
    ) -> Continuation:
        return Continuation(
            _resume=suspended.resume,
            _resume_error=suspended.resume_error,
            env=last_state.E,
            store=new_store,
        )
    
    def _get_payload_from_suspended(
        self,
        suspended: Suspended,
        last_state: CESKState,
        dispatcher: ScheduledEffectDispatcher,
    ) -> tuple[SchedulePayload, "Store"]:
        effect = suspended.effect
        handler_result = dispatcher.dispatch(effect, last_state.E, last_state.S)
        
        match handler_result:
            case Schedule(payload=p, store=s):
                return (p, s)
            case _:
                raise InterpreterInvariantError(
                    f"Expected Schedule, got {handler_result}"
                )


__all__ = [
    "RuntimeMixin",
    "EffectError",
    "RuntimeResult",
]
