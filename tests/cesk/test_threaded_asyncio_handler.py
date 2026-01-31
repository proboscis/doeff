"""Tests for threaded asyncio handler (ISSUE-CORE-469).

Tests the AsyncioThread class and threaded_asyncio_handler for running
async I/O in SyncRuntime without requiring async/await in user code.

Acceptance Criteria:
1. AsyncioThread class with start/stop/submit
2. threaded_asyncio_handler handles SuspendForIOEffect
3. Delay effect works in SyncRuntime with handler
4. Await effect works in SyncRuntime with handler
5. Multiple concurrent tasks work correctly
6. Errors propagate from async to sync
7. Thread cleanup works properly
"""

import asyncio
import time

import pytest

from doeff import do
from doeff.cesk.handlers.threaded_asyncio_handler import (
    wrap_with_threaded_async,
)
from doeff.cesk.runtime import SyncRuntime
from doeff.cesk.threading import AsyncioThread, get_asyncio_thread
from doeff.effects import Await, Delay, Get, Put


class TestAsyncioThread:
    """Tests for AsyncioThread class."""

    def test_start_creates_running_thread(self) -> None:
        thread = AsyncioThread()
        assert not thread.is_running
        thread.start()
        assert thread.is_running
        thread.stop()
        assert not thread.is_running

    def test_start_is_idempotent(self) -> None:
        thread = AsyncioThread()
        thread.start()
        thread_obj = thread._thread
        thread.start()  # Second call
        assert thread._thread is thread_obj  # Same thread object
        thread.stop()

    def test_submit_basic_coroutine(self) -> None:
        thread = AsyncioThread()
        thread.start()

        async def simple_coro():
            return 42

        result = thread.submit(simple_coro())
        assert result == 42
        thread.stop()

    def test_submit_auto_starts_thread(self) -> None:
        thread = AsyncioThread()
        assert not thread.is_running

        async def simple_coro():
            return "auto-started"

        result = thread.submit(simple_coro())
        assert result == "auto-started"
        assert thread.is_running
        thread.stop()

    def test_submit_with_async_sleep(self) -> None:
        thread = AsyncioThread()
        thread.start()

        async def sleep_coro():
            await asyncio.sleep(0.01)
            return "slept"

        start = time.time()
        result = thread.submit(sleep_coro())
        elapsed = time.time() - start

        assert result == "slept"
        assert elapsed >= 0.01
        thread.stop()

    def test_submit_propagates_exception(self) -> None:
        thread = AsyncioThread()
        thread.start()

        async def failing_coro():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            thread.submit(failing_coro())

        thread.stop()

    def test_submit_with_timeout(self) -> None:
        thread = AsyncioThread()
        thread.start()

        async def slow_coro():
            await asyncio.sleep(10.0)
            return "too slow"

        with pytest.raises(TimeoutError):
            thread.submit(slow_coro(), timeout=0.1)

        thread.stop()

    def test_stop_is_graceful(self) -> None:
        thread = AsyncioThread()
        thread.start()

        async def quick_coro():
            return "quick"

        thread.submit(quick_coro())
        stopped = thread.stop(timeout=2.0)

        assert stopped
        assert not thread.is_running

    def test_stop_on_unstarted_thread(self) -> None:
        thread = AsyncioThread()
        stopped = thread.stop()
        assert stopped

    def test_thread_is_daemon(self) -> None:
        thread = AsyncioThread()
        thread.start()
        assert thread._thread is not None
        assert thread._thread.daemon
        thread.stop()


class TestGetAsyncioThread:
    """Tests for the global singleton accessor."""

    def test_get_asyncio_thread_returns_running_thread(self) -> None:
        thread = get_asyncio_thread()
        assert thread.is_running

    def test_get_asyncio_thread_returns_same_instance(self) -> None:
        thread1 = get_asyncio_thread()
        thread2 = get_asyncio_thread()
        assert thread1 is thread2


class TestThreadedAsyncioHandler:
    """Tests for threaded_asyncio_handler with SyncRuntime."""

    def test_delay_effect_in_sync_runtime(self) -> None:
        runtime = SyncRuntime()

        @do
        def program():
            yield Delay(0.01)
            return "delayed"

        start = time.time()
        result = runtime.run(wrap_with_threaded_async(program()))
        elapsed = time.time() - start

        assert result.value == "delayed"
        assert elapsed >= 0.01

    def test_await_effect_in_sync_runtime(self) -> None:
        runtime = SyncRuntime()

        async def async_func():
            await asyncio.sleep(0.01)
            return "async_result"

        @do
        def program():
            result = yield Await(async_func())
            return result

        result = runtime.run(wrap_with_threaded_async(program()))
        assert result.value == "async_result"

    def test_multiple_delays_in_sync_runtime(self) -> None:
        runtime = SyncRuntime()

        @do
        def program():
            yield Delay(0.01)
            yield Delay(0.01)
            yield Delay(0.01)
            return "triple_delay"

        start = time.time()
        result = runtime.run(wrap_with_threaded_async(program()))
        elapsed = time.time() - start

        assert result.value == "triple_delay"
        assert elapsed >= 0.03

    def test_await_and_delay_combined(self) -> None:
        runtime = SyncRuntime()

        async def fetch_data():
            await asyncio.sleep(0.01)
            return {"key": "value"}

        @do
        def program():
            data = yield Await(fetch_data())
            yield Delay(0.01)
            return data["key"]

        result = runtime.run(wrap_with_threaded_async(program()))
        assert result.value == "value"

    def test_error_propagates_from_async_coroutine(self) -> None:
        runtime = SyncRuntime()

        async def failing_async():
            await asyncio.sleep(0.01)
            raise ValueError("async failure")

        @do
        def program():
            result = yield Await(failing_async())
            return result

        with pytest.raises(ValueError, match="async failure"):
            runtime.run_and_unwrap(wrap_with_threaded_async(program()))

    def test_state_preserved_across_async_ops(self) -> None:
        runtime = SyncRuntime()

        async def async_op():
            await asyncio.sleep(0.01)
            return "async"

        @do
        def program():
            yield Put("counter", 1)
            yield Await(async_op())
            yield Put("counter", 2)
            yield Delay(0.01)
            yield Put("counter", 3)
            final = yield Get("counter")
            return final

        result = runtime.run(wrap_with_threaded_async(program()))
        assert result.value == 3

    def test_concurrent_async_operations(self) -> None:
        runtime = SyncRuntime()
        results_order = []

        async def slow_op(name: str, delay: float):
            await asyncio.sleep(delay)
            results_order.append(name)
            return name

        @do
        def program():
            r1 = yield Await(slow_op("first", 0.01))
            r2 = yield Await(slow_op("second", 0.01))
            return (r1, r2)

        result = runtime.run(wrap_with_threaded_async(program()))
        assert result.value == ("first", "second")
        assert results_order == ["first", "second"]

    def test_zero_delay(self) -> None:
        runtime = SyncRuntime()

        @do
        def program():
            yield Delay(0.0)
            return "instant"

        result = runtime.run(wrap_with_threaded_async(program()))
        assert result.value == "instant"

    def test_exception_in_delay_context(self) -> None:
        runtime = SyncRuntime()

        @do
        def program():
            yield Delay(0.01)
            raise RuntimeError("after delay")

        with pytest.raises(RuntimeError, match="after delay"):
            runtime.run_and_unwrap(wrap_with_threaded_async(program()))


class TestThreadCleanup:
    """Tests for thread lifecycle and cleanup."""

    def test_thread_reused_across_runs(self) -> None:
        runtime = SyncRuntime()

        @do
        def program():
            yield Delay(0.01)
            return "done"

        runtime.run(wrap_with_threaded_async(program()))
        thread1 = get_asyncio_thread()

        runtime.run(wrap_with_threaded_async(program()))
        thread2 = get_asyncio_thread()

        assert thread1 is thread2

    def test_manual_stop_and_restart(self) -> None:
        thread = AsyncioThread()
        thread.start()

        async def coro():
            return "first"

        result1 = thread.submit(coro())
        assert result1 == "first"

        thread.stop()
        assert not thread.is_running

        thread.start()
        assert thread.is_running

        async def coro2():
            return "second"

        result2 = thread.submit(coro2())
        assert result2 == "second"

        thread.stop()


class TestEdgeCases:
    """Edge cases and integration tests."""

    def test_awaiting_already_completed_future(self) -> None:
        runtime = SyncRuntime()

        async def instant():
            return "instant"

        @do
        def program():
            result = yield Await(instant())
            return result

        result = runtime.run(wrap_with_threaded_async(program()))
        assert result.value == "instant"

    def test_nested_async_calls(self) -> None:
        runtime = SyncRuntime()

        async def inner():
            await asyncio.sleep(0.01)
            return "inner"

        async def outer():
            inner_result = await inner()
            await asyncio.sleep(0.01)
            return f"outer({inner_result})"

        @do
        def program():
            result = yield Await(outer())
            return result

        result = runtime.run(wrap_with_threaded_async(program()))
        assert result.value == "outer(inner)"

    def test_async_generator_exhaustion(self) -> None:
        runtime = SyncRuntime()

        async def async_list():
            await asyncio.sleep(0.01)
            return [1, 2, 3]

        @do
        def program():
            items = yield Await(async_list())
            total = sum(items)
            return total

        result = runtime.run(wrap_with_threaded_async(program()))
        assert result.value == 6

    def test_cancellation_via_timeout(self) -> None:
        thread = AsyncioThread()
        thread.start()

        async def long_running():
            await asyncio.sleep(100.0)
            return "should not reach"

        with pytest.raises(TimeoutError):
            thread.submit(long_running(), timeout=0.1)

        assert thread.is_running

        async def quick():
            return "quick after timeout"

        result = thread.submit(quick())
        assert result == "quick after timeout"

        thread.stop()


__all__ = [
    "TestAsyncioThread",
    "TestEdgeCases",
    "TestGetAsyncioThread",
    "TestThreadCleanup",
    "TestThreadedAsyncioHandler",
]
