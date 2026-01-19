"""Simulation runtime with controllable time for testing time-based effects."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, TypeVar

from doeff._vendor import FrozenDict
from doeff.cesk.runtime.base import BaseRuntime, ExecutionError
from doeff.cesk.state import CESKState
from doeff.cesk.result import Done, Failed, Suspended
from doeff.cesk.step import step
from doeff.cesk.handlers import Handler
from doeff.cesk.frames import ContinueValue, ContinueError
from doeff.cesk.errors import UnhandledEffectError
from doeff.cesk.runtime_result import RuntimeResult
from doeff.effects.time import DelayEffect, WaitUntilEffect

if TYPE_CHECKING:
    from doeff.program import Program

T = TypeVar("T")


class SimulationRuntime(BaseRuntime):
    """Runtime with controllable time for testing time-based effects.
    
    This runtime advances simulated time instantly when Delay/WaitUntil
    effects are encountered, making it suitable for testing time-dependent
    code without actual waiting.
    """

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
        program: Program[T],
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> RuntimeResult[T]:
        """Execute a program and return RuntimeResult.

        Args:
            program: The program to execute
            env: Optional initial environment (reader context)
            store: Optional initial store (mutable state)

        Returns:
            RuntimeResult containing the outcome and debugging context
        """
        initial_store = store if store is not None else {}
        initial_store = {**initial_store, "__current_time__": self._current_time}
        
        state = self._create_initial_state(program, env, initial_store)
        
        try:
            value, final_state, final_store = self._step_until_done_simulation(state)
            return self._build_success_result(value, final_state, final_store)
        except ExecutionError as err:
            # Use the state at failure point, not initial state
            return self._build_error_result(
                err.exception,
                err.final_state,
                captured_traceback=err.captured_traceback,
            )

    def run_and_unwrap(
        self,
        program: Program[T],
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> T:
        """Execute a program and return just the value (raises on error).

        This is a convenience method for when you don't need the full
        RuntimeResult context. Equivalent to `run(...).value`.

        Args:
            program: The program to execute
            env: Optional initial environment
            store: Optional initial store

        Returns:
            The program's return value

        Raises:
            Any exception raised during program execution
        """
        result = self.run(program, env, store)
        return result.value

    def _step_until_done_simulation(self, state: CESKState) -> tuple[Any, CESKState, dict[str, Any]]:
        """Step execution until completion with simulated time handling.
        
        Returns:
            Tuple of (value, final_state, final_store) on success
            
        Raises:
            ExecutionError: On failure, containing the exception and final state
        """
        while True:
            result = step(state, self._handlers)
            
            if isinstance(result, Done):
                return (result.value, state, result.store)
            
            if isinstance(result, Failed):
                exc = result.exception
                captured_tb = result.captured_traceback
                if captured_tb is not None:
                    exc.__cesk_traceback__ = captured_tb  # type: ignore[attr-defined]
                raise ExecutionError(
                    exception=exc,
                    final_state=state,
                    captured_traceback=captured_tb,
                )
            
            if isinstance(result, CESKState):
                state = result
                continue
            
            if isinstance(result, Suspended):
                main_task = state.tasks[state.main_task]
                effect = result.effect
                effect_type = type(effect)
                
                if isinstance(effect, DelayEffect):
                    self._current_time = self._current_time + timedelta(seconds=effect.seconds)
                    new_store = {**state.store, "__current_time__": self._current_time}
                    state = result.resume(None, new_store)
                    continue
                    
                if isinstance(effect, WaitUntilEffect):
                    if effect.target_time > self._current_time:
                        self._current_time = effect.target_time
                    new_store = {**state.store, "__current_time__": self._current_time}
                    state = result.resume(None, new_store)
                    continue
                
                handler = self._handlers.get(effect_type)
                if handler is not None:
                    try:
                        frame_result = handler(effect, main_task, state.store)
                    except Exception as ex:
                        state = result.resume_error(ex)
                        continue
                    
                    if isinstance(frame_result, ContinueValue):
                        state = result.resume(frame_result.value, frame_result.store)
                    elif isinstance(frame_result, ContinueError):
                        state = result.resume_error(frame_result.error)
                    else:
                        state = result.resume_error(
                            RuntimeError(f"Unexpected FrameResult: {type(frame_result)}")
                        )
                    continue
                
                state = result.resume_error(
                    UnhandledEffectError(f"No handler for {effect_type.__name__}")
                )
                continue
            
            raise RuntimeError(f"Unexpected step result: {type(result)}")


__all__ = [
    "SimulationRuntime",
]
