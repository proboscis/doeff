from typing import Any, Protocol, TypeVar

import pytest

from doeff.cesk.run import async_handlers_preset, async_run, sync_handlers_preset, sync_run
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
    """Adapter for using sync_run/async_run with the test interpreter protocol."""
    interpreter_type = "cesk"

    def run(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> RuntimeResult[T]:
        return sync_run(program, sync_handlers_preset, env=env, store=store)

    async def run_async(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> RuntimeResult[T]:
        return await async_run(program, async_handlers_preset, env=env, store=state)


@pytest.fixture
def interpreter() -> Interpreter:
    return RuntimeAdapter()


@pytest.fixture
def cesk_interpreter() -> RuntimeAdapter:
    return RuntimeAdapter()


@pytest.fixture
def pure_interpreter() -> RuntimeAdapter:
    return RuntimeAdapter()
