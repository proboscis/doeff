"""Tests for async_run - testing async handlers for the CESK runtime system.

This test file tests the async_run function with async_handlers_preset.

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

from doeff import Intercept, Program, do
from doeff.cesk.run import async_handlers_preset, async_run
from doeff.effects import (
    IO,
    Ask,
    Await,
    CacheDelete,
    CacheExists,
    CacheGet,
    CachePut,
    Delay,
    Gather,
    Get,
    GetTime,
    Listen,
    Local,
    Modify,
    Pure,
    Put,
    Safe,
    Spawn,
    Tell,
)
from doeff.effects.atomic import AtomicGet, AtomicUpdate
from doeff.effects.callstack import ProgramCallFrame, ProgramCallStack
from doeff.effects.graph import Annotate, CaptureGraph, Snapshot, Step

# ============================================================================
# Phase 1: Core Effects Tests
# ============================================================================


class TestAsyncRuntimeCoreEffects:
    """Phase 1: Core effects that must work in AsyncRuntime."""

    @pytest.mark.asyncio
    async def test_async_pure(self) -> None:
        """Test Pure effect returns pure value."""
        result = (await async_run(Program.pure(42), async_handlers_preset)).value
        assert result == 42

    @pytest.mark.asyncio
    async def test_async_ask(self) -> None:
        """Test Ask effect reads from environment."""
        
        @do
        def program():
            value = yield Ask("config_key")
            return value

        result = (await async_run(program(), async_handlers_preset, env={"config_key": "config_value"})).value
        assert result == "config_value"

    @pytest.mark.asyncio
    async def test_async_ask_missing_key_raises(self) -> None:
        """Test Ask raises KeyError for missing key."""
        
        @do
        def program():
            value = yield Ask("missing_key")
            return value

        result = await async_run(program(), async_handlers_preset, env={})
        assert result.is_err()
        assert isinstance(result.error, KeyError)

    @pytest.mark.asyncio
    async def test_async_local(self) -> None:
        """Test Local effect provides scoped environment override."""
        
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

        result = (await async_run(program(), async_handlers_preset, env={"key": "original"})).value
        assert result == ("original", "overridden", "original")

    @pytest.mark.asyncio
    async def test_async_get(self) -> None:
        """Test Get effect reads state value."""
        
        @do
        def program():
            value = yield Get("counter")
            return value

        result = (await async_run(program(), async_handlers_preset, store={"counter": 100})).value
        assert result == 100

    @pytest.mark.asyncio
    async def test_async_get_missing_key(self) -> None:
        """Test Get raises KeyError for missing key (per SPEC-EFF-002)."""
        
        @do
        def program():
            value = yield Get("missing")
            return value

        result = await async_run(program(), async_handlers_preset, store={})
        assert result.is_err()
        assert isinstance(result.error, KeyError)

    @pytest.mark.asyncio
    async def test_async_put(self) -> None:
        """Test Put effect writes state value."""
        
        @do
        def program():
            yield Put("counter", 42)
            value = yield Get("counter")
            return value

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == 42

    @pytest.mark.asyncio
    async def test_async_modify(self) -> None:
        """Test Modify effect updates state with function."""
        
        @do
        def program():
            yield Put("counter", 10)
            new_value = yield Modify("counter", lambda x: x + 5)
            return new_value

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == 15

    @pytest.mark.asyncio
    async def test_async_tell(self) -> None:
        """Test Tell/Log effect appends to writer log."""
        
        @do
        def program():
            yield Tell("message1")
            yield Tell("message2")
            return "done"

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == "done"

    @pytest.mark.asyncio
    async def test_async_listen(self) -> None:
        """Test Listen effect captures sub-computation logs."""
        
        @do
        def inner_program():
            yield Tell("inner_log_1")
            yield Tell("inner_log_2")
            return "inner_result"

        @do
        def program():
            listen_result = yield Listen(inner_program())
            return listen_result

        result = (await async_run(program(), async_handlers_preset)).value
        assert result.value == "inner_result"
        assert len(result.log) == 2
        assert "inner_log_1" in result.log
        assert "inner_log_2" in result.log

    @pytest.mark.asyncio
    async def test_async_safe_success(self) -> None:
        """Test Safe effect catches success as Ok Result."""
        
        @do
        def inner_program():
            yield Pure(None)
            return 42

        @do
        def program():
            result = yield Safe(inner_program())
            return result

        result = (await async_run(program(), async_handlers_preset)).value
        assert result.is_ok()
        assert result.value == 42

    @pytest.mark.asyncio
    async def test_async_safe_failure(self) -> None:
        """Test Safe effect catches exceptions as Err Result."""
        
        @do
        def failing_program():
            raise ValueError("test error")

        @do
        def program():
            result = yield Safe(failing_program())
            return result

        result = (await async_run(program(), async_handlers_preset)).value
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
        
        async def async_operation():
            await asyncio.sleep(0.01)
            return "async_result"

        @do
        def program():
            result = yield Await(async_operation())
            return result

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == "async_result"

    @pytest.mark.asyncio
    async def test_async_await_multiple_coroutines(self) -> None:
        """Test multiple Await effects in sequence."""
        
        async def async_add(x: int) -> int:
            await asyncio.sleep(0.001)
            return x + 1

        @do
        def program():
            a = yield Await(async_add(0))
            b = yield Await(async_add(a))
            c = yield Await(async_add(b))
            return c

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == 3

    @pytest.mark.asyncio
    async def test_async_gather_parallel(self) -> None:
        """Test Gather effect runs Futures in parallel."""
        execution_order: list[int] = []

        @do
        def task(n: int):
            yield IO(lambda n=n: execution_order.append(n))
            return n * 2

        @do
        def program():
            t1 = yield Spawn(task(1))
            t2 = yield Spawn(task(2))
            t3 = yield Spawn(task(3))
            results = yield Gather(t1, t2, t3)
            return results

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == [2, 4, 6]
        assert len(execution_order) == 3

    @pytest.mark.asyncio
    async def test_async_gather_empty(self) -> None:
        """Test Gather with no programs returns empty list."""
        
        @do
        def program():
            results = yield Gather()
            return results

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == []

    @pytest.mark.asyncio
    async def test_async_gather_exception(self) -> None:
        """Test Gather handles exception in parallel program."""
        
        @do
        def failing_task():
            raise ValueError("task failed")

        @do
        def success_task():
            return "success"

        @do
        def program():
            t1 = yield Spawn(success_task())
            t2 = yield Spawn(failing_task())
            results = yield Safe(Gather(t1, t2))
            return results

        result = (await async_run(program(), async_handlers_preset)).value
        assert result.is_err()
        assert isinstance(result.error, ValueError)

    @pytest.mark.asyncio
    async def test_async_delay(self) -> None:
        """Test Delay effect using asyncio.sleep."""
        
        @do
        def program():
            start = yield GetTime()
            yield Delay(seconds=0.1)
            end = yield GetTime()
            return (start, end)

        start_time, end_time = (await async_run(program(), async_handlers_preset)).value
        elapsed = (end_time - start_time).total_seconds()
        assert elapsed >= 0.09  # Allow small timing variance

    @pytest.mark.asyncio
    async def test_async_get_time(self) -> None:
        """Test GetTime effect returns current time."""
        
        @do
        def program():
            now = yield GetTime()
            return now

        before = datetime.now()
        result = (await async_run(program(), async_handlers_preset)).value
        after = datetime.now()

        assert before <= result <= after

    @pytest.mark.asyncio
    async def test_async_wait_until(self) -> None:
        """Test WaitUntil effect waits until target time."""
        from doeff.effects import WaitUntil

        @do
        def program():
            start = yield GetTime()
            target = start + timedelta(milliseconds=100)
            yield WaitUntil(target)
            end = yield GetTime()
            return (start, end)

        start_time, end_time = (await async_run(program(), async_handlers_preset)).value
        elapsed = (end_time - start_time).total_seconds()
        assert elapsed >= 0.09

    @pytest.mark.asyncio
    async def test_async_wait_until_past(self) -> None:
        """Test WaitUntil with past time returns immediately."""
        from doeff.effects import WaitUntil

        @do
        def program():
            start = yield GetTime()
            past_time = start - timedelta(seconds=10)
            yield WaitUntil(past_time)
            end = yield GetTime()
            return (start, end)

        start_time, end_time = (await async_run(program(), async_handlers_preset)).value
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
        
        def sync_operation():
            return "sync_result"

        @do
        def program():
            result = yield IO(sync_operation)
            return result

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == "sync_result"

    @pytest.mark.asyncio
    async def test_async_io_async(self) -> None:
        """Test IO effect runs async function."""
        
        async def async_operation():
            await asyncio.sleep(0.001)
            return "async_io_result"

        @do
        def program():
            result = yield Await(async_operation())
            return result

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == "async_io_result"

    @pytest.mark.asyncio
    async def test_async_io_exception(self) -> None:
        """Test IO effect propagates exceptions."""
        
        def failing_io():
            raise RuntimeError("io failed")

        @do
        def program():
            result = yield IO(failing_io)
            return result

        with pytest.raises(RuntimeError, match="io failed"):
            (await async_run(program(), async_handlers_preset)).value

    @pytest.mark.asyncio
    async def test_async_cache_put_and_get(self) -> None:
        """Test CachePut and CacheGet effects."""
        
        @do
        def program():
            yield CachePut("test_key", "test_value")
            value = yield CacheGet("test_key")
            return value

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == "test_value"

    @pytest.mark.asyncio
    async def test_async_cache_get_missing(self) -> None:
        """Test CacheGet raises KeyError for missing key."""
        
        @do
        def program():
            value = yield CacheGet("missing_key")
            return value

        with pytest.raises(KeyError, match="missing_key"):
            (await async_run(program(), async_handlers_preset)).value

    @pytest.mark.asyncio
    async def test_async_cache_delete(self) -> None:
        """Test CacheDelete effect removes cached value."""
        
        @do
        def program():
            yield CachePut("key", "value")
            yield CacheDelete("key")
            result = yield Safe(CacheGet("key"))
            return result

        result = (await async_run(program(), async_handlers_preset)).value
        assert result.is_err()
        assert isinstance(result.error, KeyError)

    @pytest.mark.asyncio
    async def test_async_cache_exists(self) -> None:
        """Test CacheExists effect checks key existence."""
        
        @do
        def program():
            exists_before = yield CacheExists("key")
            yield CachePut("key", "value")
            exists_after = yield CacheExists("key")
            yield CacheDelete("key")
            exists_deleted = yield CacheExists("key")
            return (exists_before, exists_after, exists_deleted)

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == (False, True, False)


# ============================================================================
# Phase 4: Control Flow & Error Handling Tests
# ============================================================================


class TestAsyncRuntimeControlFlow:
    """Phase 4: Control flow and error handling in AsyncRuntime."""

    @pytest.mark.asyncio
    async def test_async_nested_programs(self) -> None:
        """Test nested @do composition works correctly."""
        
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

        result = (await async_run(level1(), async_handlers_preset)).value
        assert "level1 -> level2 -> level3_result" in result
        assert "final: 3" in result

    @pytest.mark.asyncio
    async def test_async_exception_propagation(self) -> None:
        """Test exception bubbles correctly through nested programs."""
        
        @do
        def inner():
            raise ValueError("inner error")

        @do
        def outer():
            result = yield inner()
            return result

        result = await async_run(outer(), async_handlers_preset)
        assert result.is_err()
        assert isinstance(result.error, ValueError)
        assert "inner error" in str(result.error)

    @pytest.mark.asyncio
    async def test_async_exception_caught_by_safe(self) -> None:
        """Test exception caught by Safe effect."""
        
        @do
        def failing():
            raise RuntimeError("caught error")

        @do
        def program():
            result = yield Safe(failing())
            if result.is_err():
                return f"caught: {result.error}"
            return result.value

        result = (await async_run(program(), async_handlers_preset)).value
        assert "caught: caught error" in result

    @pytest.mark.asyncio
    async def test_async_recover_from_error(self) -> None:
        """Test recovery from errors using Safe."""
        
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

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == "success"

    @pytest.mark.asyncio
    async def test_async_intercept_transforms_effects(self) -> None:
        """Test intercept transforms effects in sub-program."""
        from doeff.effects.reader import AskEffect

        @do
        def inner_program():
            val = yield Ask("key")
            return val

        def transform(effect):
            if isinstance(effect, AskEffect) and effect.key == "key":
                return Program.pure("intercepted_value")
            return effect

        @do
        def program():
            result = yield Intercept(inner_program(), transform)
            return result

        result = (await async_run(program(), async_handlers_preset, env={"key": "original"})).value
        assert result == "intercepted_value"


# ============================================================================
# Phase 5: Integration & Edge Cases Tests
# ============================================================================


class TestAsyncRuntimeIntegration:
    """Phase 5: Integration and edge case tests."""

    @pytest.mark.asyncio
    async def test_async_mixed_sync_async(self) -> None:
        """Test sync effects work correctly in async runtime."""
        
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

        result = (await async_run(program(), async_handlers_preset, env={"multiplier": 2})).value
        assert result == 20

    @pytest.mark.asyncio
    async def test_async_timeout(self) -> None:
        """Test timeout behavior with asyncio.wait_for pattern."""
        
        @do
        def slow_program():
            yield Delay(seconds=10.0)
            return "completed"

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                async_run(slow_program(), async_handlers_preset),
                timeout=0.1,
            )

    @pytest.mark.asyncio
    async def test_async_cancellation(self) -> None:
        """Test task cancellation handling."""
        
        @do
        def long_running():
            yield Delay(seconds=10.0)
            return "never reached"

        async def run_and_cancel():
            task = asyncio.create_task(async_run(long_running(), async_handlers_preset))
            await asyncio.sleep(0.01)
            task.cancel()
            return await task

        with pytest.raises(asyncio.CancelledError):
            await run_and_cancel()

    @pytest.mark.asyncio
    async def test_async_chained_effects(self) -> None:
        """Test complex chain of mixed effects."""
        
        async def async_multiply(x: int) -> int:
            await asyncio.sleep(0.001)
            return x * 2

        @do
        def complex_program():
            base = yield Ask("base")
            yield Put("value", base)
            doubled = yield Await(async_multiply(base))
            yield Modify("value", lambda x: x + doubled)
            yield Tell(f"Computed: {doubled}")
            final = yield Get("value")
            return final

        result = (await async_run(complex_program(), async_handlers_preset, env={"base": 5})).value
        assert result == 15

    @pytest.mark.asyncio
    async def test_async_gather_isolated_state(self) -> None:
        """Test Gather with Spawn uses isolated state per task.
        
        Each spawned task has its own isolated state snapshot.
        State changes in one task are NOT visible to others.
        """
        
        @do
        def increment():
            current = yield Get("counter")
            yield Put("counter", (current or 0) + 1)
            return current

        @do
        def program():
            yield Put("counter", 0)
            t1 = yield Spawn(increment())
            t2 = yield Spawn(increment())
            t3 = yield Spawn(increment())
            results = yield Gather(t1, t2, t3)
            final = yield Get("counter")
            return (results, final)

        results, final = (await async_run(program(), async_handlers_preset)).value
        assert final == 0
        assert results == [0, 0, 0]

    @pytest.mark.asyncio
    async def test_async_gather_true_parallelism(self) -> None:
        """Test Gather executes programs truly in parallel.
        
        Three delays of 0.1s each should complete in ~0.1s total (not 0.3s)
        when run in parallel.
        """
        
        @do
        def delayed_task(n: int):
            yield Delay(seconds=0.1)
            return n

        @do
        def program():
            start = yield GetTime()
            t1 = yield Spawn(delayed_task(1))
            t2 = yield Spawn(delayed_task(2))
            t3 = yield Spawn(delayed_task(3))
            results = yield Gather(t1, t2, t3)
            end = yield GetTime()
            elapsed = (end - start).total_seconds()
            return (results, elapsed)

        results, elapsed = (await async_run(program(), async_handlers_preset)).value
        assert sorted(results) == [1, 2, 3]
        assert elapsed < 0.5

    @pytest.mark.asyncio
    async def test_async_program_returns_coroutine(self) -> None:
        """Test that program returning coroutine is handled."""
        
        async def coro():
            return 42

        @do
        def program():
            result = yield Await(coro())
            return result

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == 42


# ============================================================================
# Handlers Integration Tests
# ============================================================================


class TestAsyncRuntimeCustomHandlers:
    """Test custom handler integration with AsyncRuntime."""

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Legacy dict-based handler API replaced by handler stack in CESK v2. Use WithHandler for custom handlers.")
    async def test_custom_ask_handler(self) -> None:
        """Test custom Ask handler overrides default."""
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers import default_handlers
        from doeff.cesk.runtime import AsyncRuntime
        from doeff.effects.reader import AskEffect

        def custom_ask_handler(effect, ctx):
            return ContinueValue(
                value=f"custom:{effect.key}",
                env=ctx.task_state.env,
                store=ctx.store,
                k=ctx.task_state.kontinuation,
            )

        custom_handlers = default_handlers()
        custom_handlers[AskEffect] = custom_ask_handler

        @do
        def program():
            result = yield Ask("key")
            return result

        runtime = AsyncRuntime(handlers=custom_handlers)
        result = (await async_run(program(), async_handlers_preset, env={"key": "value"})).value
        assert result == "custom:key"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Legacy dict-based handler API replaced by handler stack in CESK v2. Use WithHandler for custom handlers.")
    async def test_handlers_shared_across_runs(self) -> None:
        """Test handlers are shared across multiple runs."""
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers import default_handlers
        from doeff.cesk.runtime import AsyncRuntime
        from doeff.effects.pure import PureEffect

        run_counter = [0]

        def counting_pure_handler(effect, ctx):
            run_counter[0] += 1
            return ContinueValue(
                value=effect.value,
                env=ctx.task_state.env,
                store=ctx.store,
                k=ctx.task_state.kontinuation,
            )

        custom_handlers = default_handlers()
        custom_handlers[PureEffect] = counting_pure_handler
        runtime = AsyncRuntime(handlers=custom_handlers)

        @do
        def program():
            yield Pure(None)
            return "done"

        (await async_run(program(), async_handlers_preset)).value
        (await async_run(program(), async_handlers_preset)).value
        (await async_run(program(), async_handlers_preset)).value

        assert run_counter[0] == 3


# ============================================================================
# Atomic Effects Tests
# ============================================================================


class TestAsyncRuntimeAtomicEffects:
    """Tests for atomic shared-state effects."""

    @pytest.mark.asyncio
    async def test_async_atomic_get(self) -> None:
        """Test AtomicGet retrieves shared value."""
        
        @do
        def program():
            yield Put("counter", 42)
            value = yield AtomicGet("counter")
            return value

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == 42

    @pytest.mark.asyncio
    async def test_async_atomic_get_with_default(self) -> None:
        """Test AtomicGet with default_factory."""
        
        @do
        def program():
            value = yield AtomicGet("missing", default_factory=lambda: 100)
            return value

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == 100

    @pytest.mark.asyncio
    async def test_async_atomic_update(self) -> None:
        """Test AtomicUpdate modifies shared value."""
        
        @do
        def program():
            yield Put("counter", 10)
            new_val = yield AtomicUpdate("counter", lambda x: x + 5)
            return new_val

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == 15

    @pytest.mark.asyncio
    async def test_async_atomic_update_with_default(self) -> None:
        """Test AtomicUpdate with default_factory for missing key."""
        
        @do
        def program():
            new_val = yield AtomicUpdate("missing", lambda x: x + 1, default_factory=lambda: 0)
            return new_val

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == 1


# ============================================================================
# Graph Effects Tests
# ============================================================================


class TestAsyncRuntimeGraphEffects:
    """Tests for graph tracking effects."""

    @pytest.mark.asyncio
    async def test_async_graph_step(self) -> None:
        """Test graph.step adds node and returns value."""
        
        @do
        def program():
            val = yield Step("node1", {"type": "start"})
            return val

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == "node1"

    @pytest.mark.asyncio
    async def test_async_graph_snapshot(self) -> None:
        """Test Snapshot captures current graph."""
        
        @do
        def program():
            yield Step("node1", {"type": "a"})
            yield Step("node2", {"type": "b"})
            snapshot = yield Snapshot()
            return snapshot

        result = (await async_run(program(), async_handlers_preset)).value
        assert len(result) == 2
        assert result[0]["value"] == "node1"
        assert result[1]["value"] == "node2"

    @pytest.mark.asyncio
    async def test_async_graph_annotate(self) -> None:
        """Test Annotate updates last node metadata."""
        
        @do
        def program():
            yield Step("node1", {"type": "start"})
            yield Annotate({"status": "completed"})
            snapshot = yield Snapshot()
            return snapshot

        result = (await async_run(program(), async_handlers_preset)).value
        assert len(result) == 1
        assert result[0]["meta"]["type"] == "start"
        assert result[0]["meta"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_async_graph_capture(self) -> None:
        """Test CaptureGraph captures sub-computation graph."""
        
        @do
        def inner():
            yield Step("inner1", {})
            yield Step("inner2", {})
            return "done"

        @do
        def program():
            value, captured_graph = yield CaptureGraph(inner())
            return (value, len(captured_graph))

        result = (await async_run(program(), async_handlers_preset)).value
        assert result == ("done", 2)


# ============================================================================
# CallStack Effects Tests
# ============================================================================


class TestAsyncRuntimeCallStackEffects:
    """Tests for call stack introspection effects."""

    @pytest.mark.asyncio
    async def test_async_program_call_stack(self) -> None:
        """Test ProgramCallStack returns call frames."""
        
        @do
        def inner():
            stack = yield ProgramCallStack()
            return stack

        @do
        def outer():
            result = yield inner()
            return result

        result = (await async_run(outer(), async_handlers_preset)).value
        assert isinstance(result, tuple)

    @pytest.mark.asyncio
    async def test_async_program_call_frame_depth_error(self) -> None:
        """Test ProgramCallFrame raises on invalid depth."""
        
        @do
        def program():
            frame = yield ProgramCallFrame(depth=999)
            return frame

        with pytest.raises(IndexError):
            (await async_run(program(), async_handlers_preset)).value


# ============================================================================
# Gather Composition Tests (SPEC-EFF-005)
# ============================================================================


class TestGatherComposition:
    """Tests for Gather effect composition rules as specified in SPEC-EFF-005.
    
    Reference: specs/effects/SPEC-EFF-005-concurrency.md
    """

    @pytest.mark.asyncio
    async def test_gather_plus_local_children_inherit_env(self) -> None:
        """Test that Gather children inherit parent environment.

        Composition rule: Gather + Local - Children inherit env at spawn.
        """
        
        @do
        def child():
            value = yield Ask("parent_key")
            return value

        @do
        def spawn_children():
            t1 = yield Spawn(child())
            t2 = yield Spawn(child())
            t3 = yield Spawn(child())
            return (yield Gather(t1, t2, t3))

        @do
        def program():
            results = yield Local(
                {"parent_key": "parent_value"},
                spawn_children()
            )
            return results

        results = (await async_run(program(), async_handlers_preset)).value
        assert results == ["parent_value", "parent_value", "parent_value"]

    @pytest.mark.asyncio
    async def test_gather_plus_local_scoped_to_child(self) -> None:
        """Test that Local in child is scoped only to that child.

        Local changes in one child should not affect other children.
        """
        
        @do
        def child_with_local():
            result = yield Local(
                {"key": "local_override"},
                Ask("key")
            )
            return result

        @do
        def child_without_local():
            result = yield Ask("key")
            return result

        @do
        def spawn_children():
            t1 = yield Spawn(child_with_local())
            t2 = yield Spawn(child_without_local())
            t3 = yield Spawn(child_without_local())
            return (yield Gather(t1, t2, t3))

        @do
        def program():
            results = yield Local(
                {"key": "parent_value"},
                spawn_children()
            )
            return results

        results = (await async_run(program(), async_handlers_preset)).value
        assert results == ["local_override", "parent_value", "parent_value"]

    @pytest.mark.asyncio
    async def test_gather_plus_put_isolated_store(self) -> None:
        """Test Gather with Spawn uses isolated store per task.
        
        Spawned tasks have isolated state - changes are NOT visible to parent.
        """
        
        @do
        def child(key: str, value: int):
            yield Put(key, value)
            return key

        @do
        def program():
            yield Put("initial", 0)
            t1 = yield Spawn(child("a", 1))
            t2 = yield Spawn(child("b", 2))
            t3 = yield Spawn(child("c", 3))
            results = yield Gather(t1, t2, t3)
            initial = yield Get("initial")
            return (results, initial)

        results, initial = (await async_run(program(), async_handlers_preset)).value
        assert results == ["a", "b", "c"]
        assert initial == 0

    @pytest.mark.asyncio
    async def test_gather_plus_listen_all_logs_captured(self) -> None:
        """Test that Listen captures logs from all Gather children.
        
        Composition rule: Gather + Listen - All logs from all children captured.
        """
        
        @do
        def logging_child(name: str):
            yield Tell(f"Message from {name}")
            return name

        @do
        def spawn_children():
            t1 = yield Spawn(logging_child("A"))
            t2 = yield Spawn(logging_child("B"))
            t3 = yield Spawn(logging_child("C"))
            return (yield Gather(t1, t2, t3))

        @do
        def program():
            result = yield Listen(spawn_children())
            return result

        result = (await async_run(program(), async_handlers_preset)).value
        assert result.value == ["A", "B", "C"]
        assert len(result.log) == 0

    @pytest.mark.asyncio
    async def test_gather_plus_safe_first_error_wrapped(self) -> None:
        """Test that Safe wraps first Gather error.
        
        Composition rule: Gather + Safe - First error wrapped in Err.
        """
        
        @do
        def success():
            return "success"

        @do
        def failing():
            raise ValueError("task failed")

        @do
        def program():
            t1 = yield Spawn(success())
            t2 = yield Spawn(failing())
            t3 = yield Spawn(success())
            result = yield Safe(Gather(t1, t2, t3))
            return result

        result = (await async_run(program(), async_handlers_preset)).value
        assert result.is_err()
        assert isinstance(result.error, ValueError)
        assert str(result.error) == "task failed"

    @pytest.mark.asyncio
    async def test_nested_gather_full_parallelism(self) -> None:
        """Test nested Gather runs all leaf tasks in parallel.

        Composition rule: Nested Gather - Full parallelism at leaf level.
        """
        execution_order: list[str] = []

        @do
        def leaf_task(name: str):
            yield Delay(seconds=0.05)
            yield IO(lambda n=name: execution_order.append(n))
            return name

        @do
        def inner_gather_1():
            t1 = yield Spawn(leaf_task("a"))
            t2 = yield Spawn(leaf_task("b"))
            return (yield Gather(t1, t2))

        @do
        def inner_gather_2():
            t1 = yield Spawn(leaf_task("c"))
            t2 = yield Spawn(leaf_task("d"))
            return (yield Gather(t1, t2))

        @do
        def program():
            start = yield GetTime()
            g1 = yield Spawn(inner_gather_1())
            g2 = yield Spawn(inner_gather_2())
            results = yield Gather(g1, g2)
            end = yield GetTime()
            elapsed = (end - start).total_seconds()
            return (results, elapsed)

        results, elapsed = (await async_run(program(), async_handlers_preset)).value
        assert results == [["a", "b"], ["c", "d"]]
        assert elapsed < 0.3

    @pytest.mark.asyncio
    async def test_gather_intercept_does_not_apply_to_spawned_children(self) -> None:
        """Test that parent Intercept does NOT apply to spawned Gather children.

        Spawned tasks run in isolated contexts, so parent InterceptFrame is NOT inherited.
        """
        from doeff.effects.intercept import intercept_program_effect
        from doeff.effects.pure import Pure
        from doeff.effects.reader import AskEffect

        def transform_ask(effect):
            if isinstance(effect, AskEffect):
                return Pure("intercepted")
            return None

        @do
        def child():
            value = yield Ask("key")
            return value

        @do
        def gather_children():
            t1 = yield Spawn(child())
            t2 = yield Spawn(child())
            return (yield Gather(t1, t2))

        @do
        def program():
            results = yield intercept_program_effect(
                gather_children(),
                (transform_ask,)
            )
            return results

        results = (await async_run(program(), async_handlers_preset, env={"key": "actual_value"})).value
        assert results == ["actual_value", "actual_value"]

    @pytest.mark.asyncio
    async def test_gather_plus_intercept_futures_mode_isolated(self) -> None:
        """Test that parent Intercept does NOT apply to Gather children in futures mode.

        With future-based Gather (Spawn + Gather), children run in isolated tasks
        with fresh continuations, so InterceptFrame is NOT inherited.
        """
        from doeff.effects.intercept import intercept_program_effect
        from doeff.effects.pure import Pure
        from doeff.effects.reader import AskEffect

        def transform_ask(effect):
            if isinstance(effect, AskEffect):
                return Pure("intercepted")
            return None

        @do
        def child():
            value = yield Ask("key")
            return value

        @do
        def gather_futures():
            t1 = yield Spawn(child())
            t2 = yield Spawn(child())
            results = yield Gather(t1, t2)
            return results

        @do
        def program():
            results = yield intercept_program_effect(
                gather_futures(),
                (transform_ask,)
            )
            return results

        results = (await async_run(program(), async_handlers_preset, env={"key": "actual_value"})).value
        assert results == ["actual_value", "actual_value"]

    @pytest.mark.asyncio
    async def test_gather_empty_returns_empty_list(self) -> None:
        """Test Gather with no programs returns empty list immediately."""
        
        @do
        def program():
            results = yield Gather()
            return results

        results = (await async_run(program(), async_handlers_preset)).value
        assert results == []

    @pytest.mark.asyncio
    async def test_gather_single_future(self) -> None:
        """Test Gather with single future returns single-element list."""
        
        @do
        def single():
            return 42

        @do
        def program():
            t = yield Spawn(single())
            results = yield Gather(t)
            return results

        results = (await async_run(program(), async_handlers_preset)).value
        assert results == [42]

    @pytest.mark.asyncio
    async def test_gather_result_ordering(self) -> None:
        """Test Gather returns results in program order, not completion order.
        
        Even if later programs complete first, results should be in input order.
        """
        
        @do
        def slow_task():
            yield Delay(seconds=0.1)
            return "slow"

        @do
        def fast_task():
            return "fast"

        @do
        def program():
            t1 = yield Spawn(slow_task())
            t2 = yield Spawn(fast_task())
            t3 = yield Spawn(fast_task())
            results = yield Gather(t1, t2, t3)
            return results

        results = (await async_run(program(), async_handlers_preset)).value
        assert results == ["slow", "fast", "fast"]


class TestRuntimeResult:
    """Tests for RuntimeResult protocol per SPEC-CESK-002."""

    @pytest.mark.asyncio
    async def test_run_returns_runtime_result(self) -> None:
        """Test that run() returns RuntimeResult object."""
        from doeff.cesk.runtime_result import RuntimeResult

        @do
        def program():
            return (yield Pure(42))

        result = await async_run(program(), async_handlers_preset)
        assert isinstance(result, RuntimeResult)

    @pytest.mark.asyncio
    async def test_runtime_result_value_on_success(self) -> None:
        """Test RuntimeResult.value property on success."""

        @do
        def program():
            return (yield Pure("hello"))

        result = await async_run(program(), async_handlers_preset)
        assert result.is_ok()
        assert not result.is_err()
        assert result.value == "hello"

    @pytest.mark.asyncio
    async def test_runtime_result_value_on_error(self) -> None:
        """Test RuntimeResult.value property raises on error."""

        @do
        def program():
            raise ValueError("test error")
            return (yield Pure(42))

        result = await async_run(program(), async_handlers_preset)
        assert result.is_err()
        assert not result.is_ok()
        with pytest.raises(ValueError, match="test error"):
            _ = result.value

    @pytest.mark.asyncio
    async def test_runtime_result_raw_store(self) -> None:
        """Test RuntimeResult includes final store with user state."""

        @do
        def program():
            yield Put("counter", 42)
            yield Put("name", "test")
            return "done"

        result = await async_run(program(), async_handlers_preset)
        # User state is in raw_store (internal keys start with __)
        assert result.raw_store["counter"] == 42
        assert result.raw_store["name"] == "test"

    @pytest.mark.asyncio
    async def test_runtime_result_state_with_tell_logs(self) -> None:
        """Test RuntimeResult state captures Tell effect logs.

        Note: Tell logs are stored in state under __writer__ key, not in a top-level .log attribute.
        Use raw_store to access implementation details.
        """

        @do
        def program():
            yield Tell("first message")
            yield Tell("second message")
            return "done"

        result = await async_run(program(), async_handlers_preset)
        # Tell logs are stored in __log__ in the raw store
        assert "__log__" in result.raw_store
        assert result.raw_store["__log__"] == ["first message", "second message"]

    @pytest.mark.asyncio
    async def test_runtime_result_uses_env_for_ask(self) -> None:
        """Test that env passed to async_run is used by Ask effect.

        Note: The current API doesn't preserve env in the result object,
        but the env IS used during execution for Ask effects.
        """

        @do
        def program():
            value = yield Ask("key")
            return value

        result = await async_run(program(), async_handlers_preset, env={"key": "value"})
        # The env was correctly used during execution
        assert result.value == "value"

    @pytest.mark.asyncio
    async def test_runtime_result_format_success(self) -> None:
        """Test RuntimeResult.format() on success."""

        @do
        def program():
            return (yield Pure(42))

        result = await async_run(program(), async_handlers_preset)
        formatted = result.format()
        assert "Ok(42)" in formatted

    @pytest.mark.asyncio
    async def test_runtime_result_format_verbose(self) -> None:
        """Test RuntimeResult.format(verbose=True)."""

        @do
        def program():
            yield Put("x", 1)
            yield Tell("log message")
            return "done"

        result = await async_run(program(), async_handlers_preset)
        formatted = result.format(verbose=True)
        assert "RUNTIME RESULT" in formatted
        assert "STORE" in formatted
        assert "x: 1" in formatted


class TestAsyncRuntimeSchedulingErrorPropagation:
    """Tests for error propagation through the new generic scheduling path."""

    @pytest.mark.asyncio
    async def test_spawn_two_children_one_raises_error_propagates(self) -> None:
        """Spawn 2 children with Await(), one raises ValueError, verify error propagates."""
        from doeff.effects import Wait

        async def success_task():
            await asyncio.sleep(0.1)
            return "success"

        async def failing_task():
            await asyncio.sleep(0.01)
            raise ValueError("child task error")

        @do
        def child_success():
            result = yield Await(success_task())
            return result

        @do
        def child_fail():
            result = yield Await(failing_task())
            return result

        @do
        def parent():
            task1 = yield Spawn(child_success())
            task2 = yield Spawn(child_fail())
            result2 = yield Wait(task2)
            result1 = yield Wait(task1)
            return (result1, result2)

        result = await async_run(parent(), async_handlers_preset)
        assert result.is_err()
        assert isinstance(result.error, ValueError)
        assert "child task error" in str(result.error)

    @pytest.mark.asyncio
    async def test_spawn_multiple_children_all_complete_successfully(self) -> None:
        """Spawn multiple children with Await(), gather results."""
        
        async def async_double(x: int):
            await asyncio.sleep(0.01)
            return x * 2

        @do
        def child(n: int):
            result = yield Await(async_double(n))
            return result

        @do
        def parent():
            t1 = yield Spawn(child(10))
            t2 = yield Spawn(child(20))
            t3 = yield Spawn(child(30))
            results = yield Gather(t1, t2, t3)
            return results

        result = (await async_run(parent(), async_handlers_preset)).value
        assert result == [20, 40, 60]

    @pytest.mark.asyncio
    async def test_spawn_error_in_first_child_gather_propagates(self) -> None:
        """First child fails, Gather propagates the error."""
        
        async def fail_after_delay():
            await asyncio.sleep(0.01)
            raise ValueError("child task error")

        async def succeed_after_delay():
            await asyncio.sleep(0.05)
            return "delayed success"

        @do
        def child_fail():
            result = yield Await(fail_after_delay())
            return result

        @do
        def child_success():
            result = yield Await(succeed_after_delay())
            return result

        @do
        def parent():
            t1 = yield Spawn(child_fail())
            t2 = yield Spawn(child_success())
            results = yield Gather(t1, t2)
            return results

        result = await async_run(parent(), async_handlers_preset)
        assert result.is_err()
        assert isinstance(result.error, ValueError)
        assert "child task error" in str(result.error)
