"""Tests for async-related effects: Await, Delay, WaitUntil, Spawn across all runtimes.

Coverage per issue ISSUE-CORE-440:
- Await effect tests for AsyncioRuntime
- Delay effect tests for all runtimes
- WaitUntil effect tests for AsyncioRuntime and SimulationRuntime
- Spawn effect tests for AsyncioRuntime and SimulationRuntime
- SyncRuntime raises AsyncEffectInSyncRuntimeError for async effects
- SimulationRuntime time advancement with Delay/WaitUntil
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from doeff import Program, do
from doeff.effects import Await, Delay, GetTime, Spawn, WaitUntil
from doeff.runtimes import (
    AsyncioRuntime,
    AsyncEffectInSyncRuntimeError,
    SimulationRuntime,
    SyncRuntime,
)


# =============================================================================
# Test Programs
# =============================================================================


@do
def await_program() -> Program[int]:
    async def async_computation():
        await asyncio.sleep(0.001)
        return 42
    result = yield Await(async_computation())
    return result


@do
def delay_program() -> Program[float]:
    start = yield GetTime()
    yield Delay(0.01)
    end = yield GetTime()
    return (end - start).total_seconds()


@do
def wait_until_program(target: datetime) -> Program[datetime]:
    yield WaitUntil(target)
    current = yield GetTime()
    return current


@do
def spawn_child() -> Program[int]:
    return 100


@do
def spawn_parent() -> Program[int]:
    task = yield Spawn(spawn_child())
    result = yield task.join()
    return result


@do
def multi_delay_program() -> Program[list[datetime]]:
    times = []
    t1 = yield GetTime()
    times.append(t1)
    yield Delay(5.0)
    t2 = yield GetTime()
    times.append(t2)
    yield Delay(10.0)
    t3 = yield GetTime()
    times.append(t3)
    return times


# =============================================================================
# AsyncioRuntime Tests - Await Effect
# =============================================================================


class TestAsyncioRuntimeAwait:
    @pytest.mark.asyncio
    async def test_await_async_coroutine(self):
        runtime = AsyncioRuntime()
        result = await runtime.run(await_program())
        assert result == 42

    @pytest.mark.asyncio
    async def test_await_simple_coroutine(self):
        @do
        def program():
            async def get_value():
                return "hello"
            value = yield Await(get_value())
            return value

        runtime = AsyncioRuntime()
        result = await runtime.run(program())
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_await_with_sleep(self):
        @do
        def program():
            async def delayed_value():
                await asyncio.sleep(0.001)
                return 123
            value = yield Await(delayed_value())
            return value

        runtime = AsyncioRuntime()
        result = await runtime.run(program())
        assert result == 123


# =============================================================================
# Delay Effect Tests - All Runtimes
# =============================================================================


class TestAsyncioRuntimeDelay:
    @pytest.mark.asyncio
    async def test_delay_executes(self):
        @do
        def program():
            yield Delay(0.001)
            return "done"

        runtime = AsyncioRuntime()
        result = await runtime.run(program())
        assert result == "done"

    @pytest.mark.asyncio
    async def test_delay_with_get_time(self):
        runtime = AsyncioRuntime()
        elapsed = await runtime.run(delay_program())
        assert elapsed >= 0.01


class TestSyncRuntimeDelay:
    def test_delay_executes_blocking(self):
        @do
        def program():
            yield Delay(0.001)
            return "done"

        runtime = SyncRuntime()
        result = runtime.run(program())
        assert result == "done"

    def test_delay_with_get_time(self):
        runtime = SyncRuntime()
        elapsed = runtime.run(delay_program())
        assert elapsed >= 0.01


class TestSimulationRuntimeDelay:
    def test_delay_advances_time_instantly(self):
        start = datetime(2024, 1, 1, 12, 0, 0)
        runtime = SimulationRuntime(start_time=start)

        @do
        def program():
            yield Delay(3600.0)
            return "done"

        result = runtime.run(program())
        assert result == "done"
        assert runtime.current_time == start + timedelta(hours=1)

    def test_delay_with_get_time_simulation(self):
        start = datetime(2024, 1, 1, 12, 0, 0)
        runtime = SimulationRuntime(start_time=start)

        @do
        def program():
            t1 = yield GetTime()
            yield Delay(30.0)
            t2 = yield GetTime()
            return (t1, t2)

        t1, t2 = runtime.run(program())
        assert t2 == t1 + timedelta(seconds=30)

    def test_multiple_delays_accumulate(self):
        start = datetime(2024, 1, 1, 12, 0, 0)
        runtime = SimulationRuntime(start_time=start)
        times = runtime.run(multi_delay_program())

        assert times[0] == start
        assert times[1] == start + timedelta(seconds=5)
        assert times[2] == start + timedelta(seconds=15)


# =============================================================================
# WaitUntil Effect Tests - AsyncioRuntime and SimulationRuntime
# =============================================================================


class TestAsyncioRuntimeWaitUntil:
    @pytest.mark.asyncio
    async def test_wait_until_past_time_returns_immediately(self):
        past = datetime.now() - timedelta(seconds=1)

        @do
        def program():
            yield WaitUntil(past)
            return "done"

        runtime = AsyncioRuntime()
        result = await runtime.run(program())
        assert result == "done"

    @pytest.mark.asyncio
    async def test_wait_until_near_future(self):
        target = datetime.now() + timedelta(milliseconds=10)

        @do
        def program():
            yield WaitUntil(target)
            return "arrived"

        runtime = AsyncioRuntime()
        result = await runtime.run(program())
        assert result == "arrived"


class TestSimulationRuntimeWaitUntil:
    def test_wait_until_advances_to_target(self):
        start = datetime(2024, 1, 1, 12, 0, 0)
        target = datetime(2024, 1, 1, 15, 0, 0)
        runtime = SimulationRuntime(start_time=start)

        result = runtime.run(wait_until_program(target))
        assert result == target
        assert runtime.current_time == target

    def test_wait_until_past_time_no_time_change(self):
        start = datetime(2024, 1, 1, 12, 0, 0)
        past = datetime(2024, 1, 1, 10, 0, 0)
        runtime = SimulationRuntime(start_time=start)

        @do
        def program():
            yield WaitUntil(past)
            t = yield GetTime()
            return t

        result = runtime.run(program())
        assert result == past


# =============================================================================
# Spawn Effect Tests - AsyncioRuntime and SimulationRuntime
# =============================================================================


class TestAsyncioRuntimeSpawn:
    @pytest.mark.asyncio
    async def test_spawn_and_join(self):
        runtime = AsyncioRuntime()
        result = await runtime.run(spawn_parent())
        assert result == 100

    @pytest.mark.asyncio
    async def test_spawn_multiple_children(self):
        @do
        def parent():
            @do
            def child(n: int):
                return n * 2

            task1 = yield Spawn(child(10))
            task2 = yield Spawn(child(20))
            r1 = yield task1.join()
            r2 = yield task2.join()
            return r1 + r2

        runtime = AsyncioRuntime()
        result = await runtime.run(parent())
        assert result == 60


# =============================================================================
# SyncRuntime Raises AsyncEffectInSyncRuntimeError
# =============================================================================


class TestSyncRuntimeAsyncErrors:
    def test_await_raises_error(self):
        from doeff.runtimes import EffectError
        runtime = SyncRuntime()
        with pytest.raises(EffectError) as exc_info:
            runtime.run(await_program())
        assert isinstance(exc_info.value.cause, AsyncEffectInSyncRuntimeError)

    def test_wait_until_raises_error(self):
        from doeff.runtimes import EffectError
        target = datetime.now() + timedelta(seconds=1)
        runtime = SyncRuntime()
        with pytest.raises(EffectError) as exc_info:
            runtime.run(wait_until_program(target))
        assert isinstance(exc_info.value.cause, AsyncEffectInSyncRuntimeError)

    def test_spawn_raises_error(self):
        from doeff.runtimes import EffectError
        runtime = SyncRuntime()
        with pytest.raises(EffectError) as exc_info:
            runtime.run(spawn_parent())
        assert isinstance(exc_info.value.cause, AsyncEffectInSyncRuntimeError)

    def test_delay_does_not_raise_error(self):
        @do
        def program():
            yield Delay(0.001)
            return "ok"

        runtime = SyncRuntime()
        result = runtime.run(program())
        assert result == "ok"


# =============================================================================
# SimulationRuntime Time Advancement Tests
# =============================================================================


class TestSimulationTimeAdvancement:
    def test_delay_advances_simulated_time(self):
        start = datetime(2024, 6, 15, 10, 0, 0)
        runtime = SimulationRuntime(start_time=start)

        @do
        def program():
            yield Delay(7200.0)
            return "done"

        runtime.run(program())
        assert runtime.current_time == datetime(2024, 6, 15, 12, 0, 0)

    def test_wait_until_advances_to_exact_time(self):
        start = datetime(2024, 6, 15, 10, 0, 0)
        target = datetime(2024, 6, 15, 18, 30, 0)
        runtime = SimulationRuntime(start_time=start)

        @do
        def program():
            yield WaitUntil(target)
            return "done"

        runtime.run(program())
        assert runtime.current_time == target

    def test_get_time_returns_simulated_time(self):
        start = datetime(2024, 6, 15, 10, 0, 0)
        runtime = SimulationRuntime(start_time=start)

        @do
        def program():
            t = yield GetTime()
            return t

        result = runtime.run(program())
        assert result == start

    def test_sequential_delays_accumulate(self):
        start = datetime(2024, 1, 1, 0, 0, 0)
        runtime = SimulationRuntime(start_time=start)

        @do
        def program():
            yield Delay(86400.0)
            yield Delay(21600.0)
            yield Delay(1800.0)
            t = yield GetTime()
            return t

        result = runtime.run(program())
        expected = start + timedelta(days=1, hours=6, minutes=30)
        assert result == expected
        assert runtime.current_time == expected

    def test_mix_delay_and_wait_until(self):
        start = datetime(2024, 1, 1, 8, 0, 0)
        runtime = SimulationRuntime(start_time=start)

        @do
        def program():
            yield Delay(7200.0)
            yield WaitUntil(datetime(2024, 1, 1, 12, 0, 0))
            yield Delay(3600.0)
            t = yield GetTime()
            return t

        result = runtime.run(program())
        assert result == datetime(2024, 1, 1, 13, 0, 0)
