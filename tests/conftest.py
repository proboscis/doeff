from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

import pytest

from doeff.cesk.runtime import SyncRuntime
from doeff.program import Program

T = TypeVar("T")


@dataclass
class TestRunResult(Generic[T]):
    """Minimal result wrapper for test compatibility.
    
    Provides the `.value`/`.is_ok`/`.error` interface that tests expect,
    while the actual runtime just returns raw values or raises.
    """
    _value: T | None
    _error: BaseException | None
    
    @property
    def value(self) -> T:
        if self._error is not None:
            raise self._error
        return self._value  # type: ignore
    
    @property
    def is_ok(self) -> bool:
        return self._error is None
    
    @property
    def error(self) -> BaseException:
        if self._error is None:
            raise ValueError("Result is Ok, no error")
        return self._error
    
    def is_err(self) -> bool:
        return self._error is not None


class Interpreter(Protocol):
    def run(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> TestRunResult[T]: ...


class RuntimeAdapter:
    interpreter_type = "cesk"

    def __init__(self) -> None:
        self._runtime = SyncRuntime()

    def run(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> TestRunResult[T]:
        try:
            value = self._runtime.run(program, env=env, store=store)
            return TestRunResult(_value=value, _error=None)
        except Exception as e:
            return TestRunResult(_value=None, _error=e)

    async def run_async(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> TestRunResult[T]:
        try:
            value = self._runtime.run(program, env=env, store=state)
            return TestRunResult(_value=value, _error=None)
        except Exception as e:
            return TestRunResult(_value=None, _error=e)


@pytest.fixture
def interpreter() -> Interpreter:
    return RuntimeAdapter()


@pytest.fixture
def cesk_interpreter() -> RuntimeAdapter:
    return RuntimeAdapter()


@pytest.fixture
def pure_interpreter() -> RuntimeAdapter:
    return RuntimeAdapter()
