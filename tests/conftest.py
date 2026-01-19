from typing import Any, Protocol, TypeVar

import pytest

from doeff.cesk.runtime import SyncRuntime
from doeff.cesk.runtime_result import RuntimeResult
from doeff.program import Program

T = TypeVar("T")


class Interpreter(Protocol):
    def run(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> RuntimeResult[T]: ...


class RuntimeAdapter:
    """Adapter for using SyncRuntime with the test interpreter protocol.
    
    Now that SyncRuntime returns RuntimeResult directly, this adapter
    simply delegates to the runtime.
    """
    interpreter_type = "cesk"

    def __init__(self) -> None:
        self._runtime = SyncRuntime()

    def run(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> RuntimeResult[T]:
        return self._runtime.run(program, env=env, store=store)

    async def run_async(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> RuntimeResult[T]:
        # SyncRuntime.run() is synchronous, but we can still return its result
        return self._runtime.run(program, env=env, store=state)


@pytest.fixture
def interpreter() -> Interpreter:
    return RuntimeAdapter()


@pytest.fixture
def cesk_interpreter() -> RuntimeAdapter:
    return RuntimeAdapter()


@pytest.fixture
def pure_interpreter() -> RuntimeAdapter:
    return RuntimeAdapter()
