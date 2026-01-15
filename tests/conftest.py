"""Pytest configuration for CESK interpreter tests."""

from typing import Any, Protocol, TypeVar

import pytest

from doeff.cesk_adapter import CESKInterpreter, CESKRunResult
from doeff.program import Program

collect_ignore = [
    "concurrency/test_spawn_backends.py",
    "concurrency/test_spawn_env_propagation.py",
    "concurrency/test_parallel_execution.py",
    "concurrency/test_gather.py",
    "concurrency/test_spawn.py",
    "concurrency/test_thread.py",
]

T = TypeVar("T")


class InterpreterResult(Protocol[T]):

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


class Interpreter(Protocol):

    async def run_async(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> InterpreterResult[T]: ...


class CESKInterpreterWrapper:

    interpreter_type = "cesk"

    def __init__(self) -> None:
        self._interpreter = CESKInterpreter()

    async def run_async(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> CESKRunResult[T]:
        return await self._interpreter.run_async(program, env=env, state=state)


@pytest.fixture
def interpreter() -> Interpreter:
    return CESKInterpreterWrapper()


@pytest.fixture
def cesk_interpreter() -> CESKInterpreter:
    return CESKInterpreter()
