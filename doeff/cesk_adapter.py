"""
CESK Interpreter Adapter - provides ProgramInterpreter-compatible interface.

This module provides a compatibility layer for running tests written for
ProgramInterpreter against the new CESK-based interpreter.

Usage:
    # Replace:
    #   engine = ProgramInterpreter()
    #   result = await engine.run_async(program())
    # With:
    #   engine = CESKInterpreter()
    #   result = await engine.run_async(program())
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from doeff._vendor import Err, FrozenDict, Ok, Result
from doeff.cesk import _run_internal, Environment, Store

T = TypeVar("T")


@dataclass
class CESKRunResult(Generic[T]):
    """
    RunResult-compatible wrapper for CESK interpreter output.

    Provides the same interface as RunResult for test compatibility:
    - .is_ok / .is_err
    - .value
    - .log
    - .state
    - .result
    """

    _result: Result[T]
    _final_store: Store = field(default_factory=dict)
    _final_env: Environment = field(default_factory=FrozenDict)

    @property
    def result(self) -> Result[T]:
        """The underlying Ok/Err result."""
        return self._result

    @property
    def is_ok(self) -> bool:
        """Check if the result is successful."""
        return isinstance(self._result, Ok)

    @property
    def is_err(self) -> bool:
        """Check if the result is an error."""
        return isinstance(self._result, Err)

    @property
    def value(self) -> T:
        """Get the successful value or raise an exception."""
        if isinstance(self._result, Ok):
            return self._result.value
        raise self._result.error

    @property
    def log(self) -> list[Any]:
        """Get the accumulated log (from __log__ in store)."""
        return self._final_store.get("__log__", [])

    @property
    def state(self) -> dict[str, Any]:
        """Get the final state (store excluding reserved keys)."""
        return {k: v for k, v in self._final_store.items() if not k.startswith("__")}

    @property
    def env(self) -> dict[Any, Any]:
        """Get the final environment."""
        return dict(self._final_env)

    @property
    def error(self) -> Exception:
        """Get the error if result is Err, otherwise raise."""
        if isinstance(self._result, Err):
            return self._result.error
        raise ValueError("Cannot access error on successful result")


class CESKInterpreter:
    """
    ProgramInterpreter-compatible wrapper for the CESK interpreter.

    Provides the same interface as ProgramInterpreter for test compatibility.
    """

    def __init__(
        self,
        env: dict[Any, Any] | FrozenDict[Any, Any] | None = None,
        state: dict[str, Any] | None = None,
    ):
        """
        Initialize the interpreter.

        Args:
            env: Initial environment (reader monad context)
            state: Initial state (state monad context)
        """
        self._initial_env = env if env is not None else {}
        self._initial_state = state if state is not None else {}

    async def run_async(
        self,
        program: "Program",
        env: dict[Any, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> CESKRunResult[T]:
        """
        Run a program asynchronously using the CESK interpreter.

        Args:
            program: The program to run
            env: Optional environment override
            state: Optional state override

        Returns:
            CESKRunResult with the result and final state/log
        """
        # Merge initial env/state with overrides
        final_env = {**self._initial_env, **(env or {})}
        final_state = {**self._initial_state, **(state or {})}

        # Convert env to FrozenDict for CESK
        if isinstance(final_env, FrozenDict):
            E = final_env
        else:
            E = FrozenDict(final_env)

        # Run through CESK
        result, final_store = await _run_internal(program, E, final_state)

        return CESKRunResult(
            _result=result,
            _final_store=final_store,
            _final_env=E,
        )

    def run(
        self,
        program: "Program",
        env: dict[Any, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> CESKRunResult[T]:
        """
        Run a program synchronously using the CESK interpreter.

        Args:
            program: The program to run
            env: Optional environment override
            state: Optional state override

        Returns:
            CESKRunResult with the result and final state/log
        """
        return asyncio.run(self.run_async(program, env, state))


# Convenience function for direct use
async def cesk_run(
    program: "Program",
    env: dict[Any, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> CESKRunResult[T]:
    """
    Run a program using CESK and return a RunResult-compatible result.

    This is equivalent to:
        engine = CESKInterpreter()
        return await engine.run_async(program)
    """
    interpreter = CESKInterpreter(env=env, state=state)
    return await interpreter.run_async(program)


def cesk_run_sync(
    program: "Program",
    env: dict[Any, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> CESKRunResult[T]:
    """
    Run a program synchronously using CESK.
    """
    return asyncio.run(cesk_run(program, env, state))


__all__ = [
    "CESKRunResult",
    "CESKInterpreter",
    "cesk_run",
    "cesk_run_sync",
]
