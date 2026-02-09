from typing import Any, Literal, Protocol, TypeVar

import pytest

from doeff import Program, async_run, default_handlers, run

T = TypeVar("T")

RunnerMode = Literal["sync", "async"]


class Interpreter(Protocol):
    def run(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> Any: ...


class RuntimeAdapter:
    """Adapter for rust-vm run/async_run with test interpreter protocol."""

    interpreter_type = "rust-vm"

    def __init__(self, mode: RunnerMode = "async") -> None:
        self.mode = mode

    def run(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> Any:
        return run(program, handlers=default_handlers(), env=env, store=store)

    async def run_async(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> Any:
        """Run program with either run or async_run based on mode.

        This allows tests to be parameterized over both runner types while
        keeping the same async test interface.
        """
        if self.mode == "sync":
            return run(program, handlers=default_handlers(), env=env, store=state)
        return await async_run(program, handlers=default_handlers(), env=env, store=state)


@pytest.fixture
def interpreter() -> Interpreter:
    """Default interpreter using async path when available."""
    return RuntimeAdapter(mode="async")


@pytest.fixture(params=["sync", "async"])
def parameterized_interpreter(request: pytest.FixtureRequest) -> RuntimeAdapter:
    """Parameterized interpreter that tests both run and async_run.

    Use this fixture to ensure effects work correctly with both runners.
    """
    return RuntimeAdapter(mode=request.param)
