"""Synchronous single-threaded runtime for the unified CESK architecture."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from doeff.cesk.runtime.base import BaseRuntime, ExecutionError
from doeff.cesk.runtime_result import RuntimeResult
from doeff.cesk.handlers import Handler

if TYPE_CHECKING:
    from doeff.program import Program

T = TypeVar("T")


class SyncRuntime(BaseRuntime):
    """Synchronous single-threaded runtime.
    
    This runtime executes programs synchronously without async support.
    Use AsyncRuntime for programs that need Gather, Delay, or Await effects.
    """

    def __init__(self, handlers: dict[type, Handler] | None = None):
        super().__init__(handlers)

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
        state = self._create_initial_state(program, env, store)
        
        try:
            value, final_state, final_store = self._step_until_done(state)
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


__all__ = [
    "SyncRuntime",
]
