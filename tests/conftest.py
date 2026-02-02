from typing import Any, Literal, Protocol, TypeVar

import pytest

from doeff.cesk.run import async_handlers_preset, async_run, sync_handlers_preset, sync_run
from doeff.cesk.runtime_result import RuntimeResult
from doeff.program import Program

T = TypeVar("T")

RunnerMode = Literal["sync", "async"]


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

    def __init__(self, mode: RunnerMode = "async") -> None:
        self.mode = mode

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
        """Run program with either sync_run or async_run based on mode.

        This allows tests to be parameterized over both runner types while
        keeping the same async test interface.
        """
        if self.mode == "sync":
            # Wrap sync_run result - runs synchronously but returns via coroutine
            return sync_run(program, sync_handlers_preset, env=env, store=state)
        else:
            return await async_run(program, async_handlers_preset, env=env, store=state)


@pytest.fixture
def interpreter() -> Interpreter:
    """Default interpreter using async_run (backwards compatible)."""
    return RuntimeAdapter(mode="async")


@pytest.fixture(params=["sync", "async"])
def parameterized_interpreter(request: pytest.FixtureRequest) -> RuntimeAdapter:
    """Parameterized interpreter that tests both sync_run and async_run.

    Use this fixture to ensure effects work correctly with both runners.
    """
    return RuntimeAdapter(mode=request.param)


@pytest.fixture
def cesk_interpreter() -> RuntimeAdapter:
    return RuntimeAdapter(mode="async")


@pytest.fixture
def pure_interpreter() -> RuntimeAdapter:
    return RuntimeAdapter(mode="async")
