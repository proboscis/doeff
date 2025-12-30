"""
Pytest configuration for CESK interpreter tests.

Provides parameterized fixtures to test both PureInterpreter (ProgramInterpreter)
and CESKInterpreter with the same test cases.
"""

from typing import Any, Protocol, TypeVar

import pytest

from doeff._types_internal import ExecutionContext, RunResult
from doeff._vendor import Err
from doeff.cesk_adapter import CESKInterpreter, CESKRunResult
from doeff.interpreter import ProgramInterpreter
from doeff.program import Program

T = TypeVar("T")


class InterpreterResult(Protocol[T]):
    """Protocol for interpreter result types."""

    @property
    def is_ok(self) -> bool: ...

    @property
    def is_err(self) -> bool: ...

    @property
    def value(self) -> T: ...

    @property
    def log(self) -> list[Any]: ...

    @property
    def state(self) -> dict[str, Any]: ...

    @property
    def env(self) -> dict[Any, Any]: ...

    @property
    def error(self) -> Exception: ...


class RunResultWrapper(InterpreterResult[T]):
    """Wrapper for RunResult that adds .error property."""

    def __init__(self, result: RunResult[T]):
        self._result = result

    @property
    def is_ok(self) -> bool:
        return self._result.is_ok

    @property
    def is_err(self) -> bool:
        return self._result.is_err

    @property
    def value(self) -> T:
        return self._result.value

    @property
    def log(self) -> list[Any]:
        return list(self._result.log)

    @property
    def state(self) -> dict[str, Any]:
        return self._result.state

    @property
    def env(self) -> dict[Any, Any]:
        return self._result.env

    @property
    def error(self) -> Exception:
        """Get the error if result is Err, otherwise raise."""
        if isinstance(self._result.result, Err):
            return self._result.result.error
        raise ValueError("Cannot access error on successful result")


class Interpreter(Protocol):
    """Protocol for interpreter types."""

    async def run_async(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> InterpreterResult[T]: ...


class PureInterpreterAdapter:
    """Adapter for ProgramInterpreter that matches CESKInterpreter interface."""

    interpreter_type = "pure"

    def __init__(self) -> None:
        self._interpreter = ProgramInterpreter()

    async def run_async(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> RunResultWrapper[T]:
        """Run program with env/state kwargs like CESKInterpreter."""
        context = ExecutionContext(
            env=env or {},
            state=state or {},
        )
        result = await self._interpreter.run_async(program, context)
        return RunResultWrapper(result)


class CESKInterpreterWrapper:
    """Wrapper for CESKInterpreter that adds interpreter_type."""

    interpreter_type = "cesk"

    def __init__(self) -> None:
        self._interpreter = CESKInterpreter()

    async def run_async(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> CESKRunResult[T]:
        """Run program, delegating to CESKInterpreter."""
        return await self._interpreter.run_async(program, env=env, state=state)


@pytest.fixture(params=["cesk", "pure"])
def interpreter(request: pytest.FixtureRequest) -> Interpreter:
    """
    Parameterized fixture providing both interpreter implementations.

    This allows the same tests to run against both CESKInterpreter and
    ProgramInterpreter to ensure behavioral compatibility.

    Each interpreter has an `interpreter_type` attribute ("cesk" or "pure")
    that can be used to skip tests for specific implementations.
    """
    if request.param == "cesk":
        return CESKInterpreterWrapper()
    else:
        return PureInterpreterAdapter()


@pytest.fixture
def cesk_interpreter() -> CESKInterpreter:
    """Fixture providing only CESK interpreter (for CESK-specific tests)."""
    return CESKInterpreter()


@pytest.fixture
def pure_interpreter() -> PureInterpreterAdapter:
    """Fixture providing only Pure interpreter (for Pure-specific tests)."""
    return PureInterpreterAdapter()
