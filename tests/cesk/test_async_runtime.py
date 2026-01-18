"""Tests for AsyncRuntime - implementing AsyncRuntime for the CESK runtime system.

This test file follows a test-first approach where all tests are created first
and expected to FAIL until the AsyncRuntime implementation is complete.

Test Matrix Phases:
- Phase 1: Core Effects (Ask, Local, Get, Put, Modify, Tell, Listen, Pure, Safe)
- Phase 2: Async-Specific Effects (Await, Gather, Delay, GetTime)
- Phase 3: IO & Cache Effects (IO sync/async, CacheGet, CachePut, CacheDelete)
- Phase 4: Control Flow & Error Handling (Intercept, nested programs, exception propagation)
- Phase 5: Integration & Edge Cases (mixed sync/async, cancellation, timeout, concurrent state)

Reference: gh#151
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from doeff import do, Program
from doeff.effects import (
    Ask,
    Await,
    CacheDelete,
    CacheGet,
    CachePut,
    Delay,
    Gather,
    Get,
    GetTime,
    IO,
    Listen,
    Local,
    Log,
    Modify,
    Pure,
    Put,
    Safe,
    Tell,
)


# ============================================================================
# Phase 1: Core Effects Tests
# ============================================================================


class TestAsyncRuntimeCoreEffects:
    """Phase 1: Core effects that must work in AsyncRuntime."""

    @pytest.mark.asyncio
    async def test_async_pure(self) -> None:
        """Test Pure effect returns pure value."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()
        result = await runtime.run(Program.pure(42))
        assert result == 42

    @pytest.mark.asyncio
    async def test_async_ask(self) -> None:
        """Test Ask effect reads from environment."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def program():
            value = yield Ask("config_key")
            return value

        result = await runtime.run(program(), env={"config_key": "config_value"})
        assert result == "config_value"

    @pytest.mark.asyncio
    async def test_async_ask_missing_key_raises(self) -> None:
        """Test Ask raises KeyError for missing key."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def program():
            value = yield Ask("missing_key")
            return value

        with pytest.raises(KeyError, match="missing_key"):
            await runtime.run(program(), env={})

    @pytest.mark.asyncio
    async def test_async_local(self) -> None:
        """Test Local effect provides scoped environment override."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def inner_program():
            value = yield Ask("key")
            return value

        @do
        def program():
            outer = yield Ask("key")
            inner = yield Local({"key": "overridden"}, inner_program())
            after = yield Ask("key")
            return (outer, inner, after)

        result = await runtime.run(program(), env={"key": "original"})
        assert result == ("original", "overridden", "original")

    @pytest.mark.asyncio
    async def test_async_get(self) -> None:
        """Test Get effect reads state value."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def program():
            value = yield Get("counter")
            return value

        result = await runtime.run(program(), store={"counter": 100})
        assert result == 100

    @pytest.mark.asyncio
    async def test_async_get_missing_key(self) -> None:
        """Test Get returns None for missing key."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def program():
            value = yield Get("missing")
            return value

        result = await runtime.run(program(), store={})
        assert result is None

    @pytest.mark.asyncio
    async def test_async_put(self) -> None:
        """Test Put effect writes state value."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def program():
            yield Put("counter", 42)
            value = yield Get("counter")
            return value

        result = await runtime.run(program())
        assert result == 42

    @pytest.mark.asyncio
    async def test_async_modify(self) -> None:
        """Test Modify effect updates state with function."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def program():
            yield Put("counter", 10)
            new_value = yield Modify("counter", lambda x: x + 5)
            return new_value

        result = await runtime.run(program())
        assert result == 15

    @pytest.mark.asyncio
    async def test_async_tell(self) -> None:
        """Test Tell/Log effect appends to writer log."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def program():
            yield Log("message1")
            yield Tell("message2")
            return "done"

        result = await runtime.run(program())
        assert result == "done"

    @pytest.mark.asyncio
    async def test_async_listen(self) -> None:
        """Test Listen effect captures sub-computation logs."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def inner_program():
            yield Log("inner_log_1")
            yield Log("inner_log_2")
            return "inner_result"

        @do
        def program():
            listen_result = yield Listen(inner_program())
            return listen_result

        result = await runtime.run(program())
        assert result.value == "inner_result"
        assert len(result.log) == 2
        assert "inner_log_1" in result.log
        assert "inner_log_2" in result.log

    @pytest.mark.asyncio
    async def test_async_safe_success(self) -> None:
        """Test Safe effect catches success as Ok Result."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def inner_program():
            yield Pure(None)
            return 42

        @do
        def program():
            result = yield Safe(inner_program())
            return result

        result = await runtime.run(program())
        assert result.is_ok()
        assert result.value == 42

    @pytest.mark.asyncio
    async def test_async_safe_failure(self) -> None:
        """Test Safe effect catches exceptions as Err Result."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def failing_program():
            raise ValueError("test error")

        @do
        def program():
            result = yield Safe(failing_program())
            return result

        result = await runtime.run(program())
        assert result.is_err()
        assert isinstance(result.error, ValueError)
        assert str(result.error) == "test error"


# ============================================================================
# Phase 2: Async-Specific Effects Tests
# ============================================================================


class TestAsyncRuntimeAsyncEffects:
    """Phase 2: Async-specific effects that differentiate AsyncRuntime."""

    @pytest.mark.asyncio
    async def test_async_await_coroutine(self) -> None:
        """Test Await effect awaits native coroutine."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        async def async_operation():
            await asyncio.sleep(0.01)
            return "async_result"

        @do
        def program():
            result = yield Await(async_operation())
            return result

        result = await runtime.run(program())
        assert result == "async_result"

    @pytest.mark.asyncio
    async def test_async_await_multiple_coroutines(self) -> None:
        """Test multiple Await effects in sequence."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        async def async_add(x: int) -> int:
            await asyncio.sleep(0.001)
            return x + 1

        @do
        def program():
            a = yield Await(async_add(0))
            b = yield Await(async_add(a))
            c = yield Await(async_add(b))
            return c

        result = await runtime.run(program())
        assert result == 3

    @pytest.mark.asyncio
    async def test_async_gather_parallel(self) -> None:
        """Test Gather effect runs programs in parallel."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        execution_order: list[int] = []

        @do
        def task(n: int):
            yield IO(lambda n=n: execution_order.append(n))
            return n * 2

        @do
        def program():
            results = yield Gather(task(1), task(2), task(3))
            return results

        result = await runtime.run(program())
        assert result == [2, 4, 6]
        assert len(execution_order) == 3

    @pytest.mark.asyncio
    async def test_async_gather_empty(self) -> None:
        """Test Gather with no programs returns empty list."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def program():
            results = yield Gather()
            return results

        result = await runtime.run(program())
        assert result == []

    @pytest.mark.asyncio
    async def test_async_gather_exception(self) -> None:
        """Test Gather handles exception in parallel program."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def failing_task():
            raise ValueError("task failed")

        @do
        def success_task():
            return "success"

        @do
        def program():
            results = yield Safe(Gather(success_task(), failing_task()))
            return results

        result = await runtime.run(program())
        assert result.is_err()
        assert isinstance(result.error, ValueError)

    @pytest.mark.asyncio
    async def test_async_delay(self) -> None:
        """Test Delay effect using asyncio.sleep."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def program():
            start = yield GetTime()
            yield Delay(seconds=0.1)
            end = yield GetTime()
            return (start, end)

        start_time, end_time = await runtime.run(program())
        elapsed = (end_time - start_time).total_seconds()
        assert elapsed >= 0.09  # Allow small timing variance

    @pytest.mark.asyncio
    async def test_async_get_time(self) -> None:
        """Test GetTime effect returns current time."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def program():
            now = yield GetTime()
            return now

        before = datetime.now()
        result = await runtime.run(program())
        after = datetime.now()

        assert before <= result <= after

    @pytest.mark.asyncio
    async def test_async_wait_until(self) -> None:
        """Test WaitUntil effect waits until target time."""
        from doeff.cesk.runtime import AsyncRuntime
        from doeff.effects import WaitUntil
        from datetime import timedelta

        runtime = AsyncRuntime()

        @do
        def program():
            start = yield GetTime()
            target = start + timedelta(milliseconds=100)
            yield WaitUntil(target)
            end = yield GetTime()
            return (start, end)

        start_time, end_time = await runtime.run(program())
        elapsed = (end_time - start_time).total_seconds()
        assert elapsed >= 0.09

    @pytest.mark.asyncio
    async def test_async_wait_until_past(self) -> None:
        """Test WaitUntil with past time returns immediately."""
        from doeff.cesk.runtime import AsyncRuntime
        from doeff.effects import WaitUntil
        from datetime import timedelta

        runtime = AsyncRuntime()

        @do
        def program():
            start = yield GetTime()
            past_time = start - timedelta(seconds=10)
            yield WaitUntil(past_time)
            end = yield GetTime()
            return (start, end)

        start_time, end_time = await runtime.run(program())
        elapsed = (end_time - start_time).total_seconds()
        assert elapsed < 0.1


# ============================================================================
# Phase 3: IO & Cache Effects Tests
# ============================================================================


class TestAsyncRuntimeIOCacheEffects:
    """Phase 3: IO and Cache effects in AsyncRuntime."""

    @pytest.mark.asyncio
    async def test_async_io_sync(self) -> None:
        """Test IO effect runs sync function."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        def sync_operation():
            return "sync_result"

        @do
        def program():
            result = yield IO(sync_operation)
            return result

        result = await runtime.run(program())
        assert result == "sync_result"

    @pytest.mark.asyncio
    async def test_async_io_async(self) -> None:
        """Test IO effect runs async function."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        async def async_operation():
            await asyncio.sleep(0.001)
            return "async_io_result"

        @do
        def program():
            result = yield Await(async_operation())
            return result

        result = await runtime.run(program())
        assert result == "async_io_result"

    @pytest.mark.asyncio
    async def test_async_io_exception(self) -> None:
        """Test IO effect propagates exceptions."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        def failing_io():
            raise RuntimeError("io failed")

        @do
        def program():
            result = yield IO(failing_io)
            return result

        with pytest.raises(RuntimeError, match="io failed"):
            await runtime.run(program())

    @pytest.mark.asyncio
    async def test_async_cache_put_and_get(self) -> None:
        """Test CachePut and CacheGet effects."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def program():
            yield CachePut("test_key", "test_value")
            value = yield CacheGet("test_key")
            return value

        result = await runtime.run(program())
        assert result == "test_value"

    @pytest.mark.asyncio
    async def test_async_cache_get_missing(self) -> None:
        """Test CacheGet raises KeyError for missing key."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def program():
            value = yield CacheGet("missing_key")
            return value

        with pytest.raises(KeyError, match="missing_key"):
            await runtime.run(program())

    @pytest.mark.asyncio
    async def test_async_cache_delete(self) -> None:
        """Test CacheDelete effect removes cached value."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def program():
            yield CachePut("key", "value")
            yield CacheDelete("key")
            result = yield Safe(CacheGet("key"))
            return result

        result = await runtime.run(program())
        assert result.is_err()
        assert isinstance(result.error, KeyError)


# ============================================================================
# Phase 4: Control Flow & Error Handling Tests
# ============================================================================


class TestAsyncRuntimeControlFlow:
    """Phase 4: Control flow and error handling in AsyncRuntime."""

    @pytest.mark.asyncio
    async def test_async_nested_programs(self) -> None:
        """Test nested @do composition works correctly."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def level3():
            yield Put("level", 3)
            return "level3_result"

        @do
        def level2():
            yield Put("level", 2)
            result = yield level3()
            return f"level2 -> {result}"

        @do
        def level1():
            yield Put("level", 1)
            result = yield level2()
            final_level = yield Get("level")
            return f"level1 -> {result} (final: {final_level})"

        result = await runtime.run(level1())
        assert "level1 -> level2 -> level3_result" in result
        assert "final: 3" in result

    @pytest.mark.asyncio
    async def test_async_exception_propagation(self) -> None:
        """Test exception bubbles correctly through nested programs."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def inner():
            raise ValueError("inner error")

        @do
        def outer():
            result = yield inner()
            return result

        with pytest.raises(ValueError, match="inner error"):
            await runtime.run(outer())

    @pytest.mark.asyncio
    async def test_async_exception_caught_by_safe(self) -> None:
        """Test exception caught by Safe effect."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def failing():
            raise RuntimeError("caught error")

        @do
        def program():
            result = yield Safe(failing())
            if result.is_err():
                return f"caught: {result.error}"
            return result.value

        result = await runtime.run(program())
        assert "caught: caught error" in result

    @pytest.mark.asyncio
    async def test_async_recover_from_error(self) -> None:
        """Test recovery from errors using Safe."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def may_fail(succeed: bool):
            if succeed:
                return "success"
            raise ValueError("failed")

        @do
        def program():
            result1 = yield Safe(may_fail(False))
            if result1.is_err():
                result2 = yield Safe(may_fail(True))
                return result2.value if result2.is_ok() else "all failed"
            return result1.value

        result = await runtime.run(program())
        assert result == "success"


# ============================================================================
# Phase 5: Integration & Edge Cases Tests
# ============================================================================


class TestAsyncRuntimeIntegration:
    """Phase 5: Integration and edge case tests."""

    @pytest.mark.asyncio
    async def test_async_mixed_sync_async(self) -> None:
        """Test sync effects work correctly in async runtime."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        async def async_fetch():
            await asyncio.sleep(0.001)
            return 10

        @do
        def program():
            yield Put("counter", 0)
            config = yield Ask("multiplier")
            fetched = yield Await(async_fetch())
            yield Modify("counter", lambda x: x + fetched)
            final = yield Get("counter")
            return final * config

        result = await runtime.run(program(), env={"multiplier": 2})
        assert result == 20

    @pytest.mark.asyncio
    async def test_async_timeout(self) -> None:
        """Test timeout behavior with asyncio.wait_for pattern."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def slow_program():
            yield Delay(seconds=10.0)
            return "completed"

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                runtime.run(slow_program()),
                timeout=0.1,
            )

    @pytest.mark.asyncio
    async def test_async_cancellation(self) -> None:
        """Test task cancellation handling."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def long_running():
            yield Delay(seconds=10.0)
            return "never reached"

        async def run_and_cancel():
            task = asyncio.create_task(runtime.run(long_running()))
            await asyncio.sleep(0.01)
            task.cancel()
            return await task

        with pytest.raises(asyncio.CancelledError):
            await run_and_cancel()

    @pytest.mark.asyncio
    async def test_async_chained_effects(self) -> None:
        """Test complex chain of mixed effects."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        async def async_multiply(x: int) -> int:
            await asyncio.sleep(0.001)
            return x * 2

        @do
        def complex_program():
            base = yield Ask("base")
            yield Put("value", base)
            doubled = yield Await(async_multiply(base))
            yield Modify("value", lambda x: x + doubled)
            yield Log(f"Computed: {doubled}")
            final = yield Get("value")
            return final

        result = await runtime.run(
            complex_program(),
            env={"base": 5},
        )
        assert result == 15

    @pytest.mark.asyncio
    async def test_async_concurrent_gather_with_state(self) -> None:
        """Test Gather with state access (sequential in current impl)."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def increment():
            current = yield Get("counter")
            yield Put("counter", (current or 0) + 1)
            return current

        @do
        def program():
            yield Put("counter", 0)
            results = yield Gather(increment(), increment(), increment())
            final = yield Get("counter")
            return (results, final)

        results, final = await runtime.run(program())
        assert final == 3
        assert results == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_async_program_returns_coroutine(self) -> None:
        """Test that program returning coroutine is handled."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        async def coro():
            return 42

        @do
        def program():
            result = yield Await(coro())
            return result

        result = await runtime.run(program())
        assert result == 42


# ============================================================================
# Handlers Integration Tests
# ============================================================================


class TestAsyncRuntimeCustomHandlers:
    """Test custom handler integration with AsyncRuntime."""

    @pytest.mark.asyncio
    async def test_custom_ask_handler(self) -> None:
        """Test custom Ask handler overrides default."""
        from doeff.cesk.runtime import AsyncRuntime
        from doeff.cesk.handlers import default_handlers
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.reader import AskEffect

        def custom_ask_handler(effect, task_state, store):
            return ContinueValue(
                value=f"custom:{effect.key}",
                env=task_state.env,
                store=store,
                k=task_state.kontinuation,
            )

        custom_handlers = default_handlers()
        custom_handlers[AskEffect] = custom_ask_handler

        @do
        def program():
            result = yield Ask("key")
            return result

        runtime = AsyncRuntime(handlers=custom_handlers)
        result = await runtime.run(program(), env={"key": "value"})
        assert result == "custom:key"

    @pytest.mark.asyncio
    async def test_handlers_shared_across_runs(self) -> None:
        """Test handlers are shared across multiple runs."""
        from doeff.cesk.runtime import AsyncRuntime
        from doeff.cesk.handlers import default_handlers
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.pure import PureEffect

        run_counter = [0]

        def counting_pure_handler(effect, task_state, store):
            run_counter[0] += 1
            return ContinueValue(
                value=effect.value,
                env=task_state.env,
                store=store,
                k=task_state.kontinuation,
            )

        custom_handlers = default_handlers()
        custom_handlers[PureEffect] = counting_pure_handler
        runtime = AsyncRuntime(handlers=custom_handlers)

        @do
        def program():
            yield Pure(None)
            return "done"

        await runtime.run(program())
        await runtime.run(program())
        await runtime.run(program())

        assert run_counter[0] == 3
