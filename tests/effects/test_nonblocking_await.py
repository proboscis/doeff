"""Tests for nonblocking_await_handler.

Verifies that PythonAsyncioAwaitEffect is handled non-blockingly via
a persistent background event loop and ExternalPromise.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from doeff import Await, Gather, Spawn, async_run, default_handlers, do, run
from doeff.nonblocking_await import (
    get_loop,
    with_nonblocking_await,
)


def _run_nb(program: Any, timeout: float = 2.0) -> Any:
    """Run a program with nonblocking await, guarded by a timeout."""
    wrapped = with_nonblocking_await(program)
    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _worker() -> None:
        try:
            result["value"] = run(wrapped, handlers=default_handlers())
        except BaseException as exc:
            error["value"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    assert not thread.is_alive(), "program timed out"

    if "value" in error:
        raise error["value"]

    return result["value"]


class TestNonblockingAwaitBasic:
    """Basic functionality: single awaitable resolves correctly."""

    def test_await_coroutine_returns_value(self) -> None:
        """yield Await(coro()) returns the coroutine's result."""

        async def greet() -> str:
            return "hello"

        @do
        def program():
            result = yield Await(greet())
            return result

        result = _run_nb(program())
        assert result.is_ok
        assert result.value == "hello"

    def test_await_coroutine_with_sleep(self) -> None:
        """Awaitable that does real async I/O (sleep) completes."""

        async def delayed_value() -> int:
            await asyncio.sleep(0.05)
            return 42

        @do
        def program():
            result = yield Await(delayed_value())
            return result

        result = _run_nb(program())
        assert result.is_ok
        assert result.value == 42

    def test_await_returns_none(self) -> None:
        """Awaitable returning None works correctly."""

        async def noop() -> None:
            pass

        @do
        def program():
            result = yield Await(noop())
            return result

        result = _run_nb(program())
        assert result.is_ok
        assert result.value is None


class TestNonblockingAwaitExceptionPropagation:
    """Exceptions from awaitables propagate correctly."""

    def test_exception_propagates(self) -> None:
        """Exception raised in awaitable is captured."""

        async def failing() -> None:
            raise ValueError("async boom")

        @do
        def program():
            result = yield Await(failing())
            return result

        result = _run_nb(program())
        assert result.is_err()
        assert isinstance(result.error, ValueError)
        assert "async boom" in str(result.error)

    def test_runtime_error_propagates(self) -> None:
        """RuntimeError from awaitable is captured."""

        async def bad_state() -> None:
            raise RuntimeError("invalid state")

        @do
        def program():
            result = yield Await(bad_state())
            return result

        result = _run_nb(program())
        assert result.is_err()
        assert isinstance(result.error, RuntimeError)


class TestNonblockingAwaitConcurrency:
    """Multiple spawned tasks with Await run concurrently."""

    def test_spawn_gather_does_not_violate_one_shot(self) -> None:
        async def slow_value(val: str) -> str:
            await asyncio.sleep(0.05)
            return val

        @do
        def child_a():
            return (yield Await(slow_value("a")))

        @do
        def child_b():
            return (yield Await(slow_value("b")))

        @do
        def program():
            t1 = yield Spawn(child_a())
            t2 = yield Spawn(child_b())
            values = yield Gather(t1, t2)
            return tuple(values)

        result = _run_nb(program())
        assert result.is_ok
        assert result.value == ("a", "b")

    def test_concurrent_via_asyncio_gather(self) -> None:
        """Concurrent awaitables via asyncio.gather inside a single Await."""

        async def slow_value(val: str) -> str:
            await asyncio.sleep(0.1)
            return val

        async def gather_both() -> tuple[str, str]:
            a, b = await asyncio.gather(slow_value("a"), slow_value("b"))
            return (a, b)

        @do
        def program():
            results = yield Await(gather_both())
            return results

        start = time.monotonic()
        result = _run_nb(program())
        elapsed = time.monotonic() - start

        assert result.is_ok
        assert result.value == ("a", "b")
        # If truly concurrent, total time < 2 * 0.1s.
        assert elapsed < 0.5, f"Expected concurrent execution, took {elapsed:.2f}s"

    def test_multiple_sequential_awaits(self) -> None:
        """Multiple sequential Await calls in one task work correctly."""

        async def double(x: int) -> int:
            return x * 2

        @do
        def program():
            a = yield Await(double(1))
            b = yield Await(double(2))
            c = yield Await(double(3))
            return (a, b, c)

        result = _run_nb(program())
        assert result.is_ok
        assert result.value == (2, 4, 6)


class TestNonblockingAwaitLoopSingleton:
    """The background event loop is a singleton."""

    def test_loop_is_same_across_calls(self) -> None:
        """get_loop() returns the same loop instance."""
        loop1 = get_loop()
        loop2 = get_loop()
        assert loop1 is loop2

    def test_loop_is_running(self) -> None:
        """The singleton loop is actually running."""
        loop = get_loop()
        assert loop.is_running()

    def test_awaitables_share_loop(self) -> None:
        """Multiple awaitables run on the same event loop."""

        async def capture_loop_id() -> int:
            loop = asyncio.get_running_loop()
            return id(loop)

        @do
        def program():
            id1 = yield Await(capture_loop_id())
            id2 = yield Await(capture_loop_id())
            return (id1, id2)

        result = _run_nb(program())
        assert result.is_ok
        id1, id2 = result.value
        assert id1 == id2, "Awaitables should run on the same event loop"


class TestNonblockingAwaitWithAsyncRun:
    """Works with async_run as well."""

    @pytest.mark.asyncio
    async def test_async_run_with_nonblocking_await(self) -> None:
        """nonblocking await handler works under async_run."""

        async def compute() -> int:
            await asyncio.sleep(0.01)
            return 99

        @do
        def program():
            result = yield Await(compute())
            return result

        wrapped = with_nonblocking_await(program())
        result = await async_run(wrapped, handlers=default_handlers())
        assert result.is_ok
        assert result.value == 99
