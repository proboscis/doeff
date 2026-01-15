"""Tests for unified time effects: Delay, WaitUntil, Spawn across all runtimes."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from doeff.do import do
from doeff.effects import Delay, DelayEffect, Get, Put, WaitUntil, WaitUntilEffect
from doeff.runtimes import AsyncioRuntime, SimulationRuntime, SyncRuntime


class TestDelayEffect:
    def test_delay_effect_creation(self):
        effect = DelayEffect(seconds=5.0)
        assert effect.seconds == 5.0

    def test_delay_effect_rejects_negative(self):
        with pytest.raises(ValueError, match="non-negative"):
            DelayEffect(seconds=-1.0)

    def test_delay_intercept_returns_self(self):
        effect = DelayEffect(seconds=1.0)
        intercepted = effect.intercept(lambda e: e)
        assert intercepted is effect


class TestWaitUntilEffect:
    def test_wait_until_effect_creation(self):
        target = datetime(2025, 6, 15, 12, 0, 0)
        effect = WaitUntilEffect(target_time=target)
        assert effect.target_time == target

    def test_wait_until_intercept_returns_self(self):
        target = datetime(2025, 6, 15, 12, 0, 0)
        effect = WaitUntilEffect(target_time=target)
        intercepted = effect.intercept(lambda e: e)
        assert intercepted is effect


class TestDelayWithSimulationRuntime:
    def test_delay_advances_simulation_time(self):
        start = datetime(2025, 1, 1, 12, 0, 0)

        @do
        def program_with_delay():
            yield Delay(seconds=10.0)
            return "done"

        runtime = SimulationRuntime(start_time=start)
        result = runtime.run(program_with_delay())

        assert result == "done"
        assert runtime.current_time == start + timedelta(seconds=10)

    def test_multiple_delays_accumulate_time(self):
        start = datetime(2025, 1, 1, 12, 0, 0)

        @do
        def program_with_delays():
            yield Delay(seconds=5.0)
            yield Delay(seconds=10.0)
            yield Delay(seconds=3.0)
            return "done"

        runtime = SimulationRuntime(start_time=start)
        result = runtime.run(program_with_delays())

        assert result == "done"
        assert runtime.current_time == start + timedelta(seconds=18)

    def test_delay_with_state_effects(self):
        start = datetime(2025, 1, 1, 12, 0, 0)

        @do
        def program_with_delay_and_state():
            yield Put("counter", 0)
            yield Delay(seconds=5.0)
            count = yield Get("counter")
            yield Put("counter", count + 1)
            yield Delay(seconds=5.0)
            return (yield Get("counter"))

        runtime = SimulationRuntime(start_time=start)
        result = runtime.run(program_with_delay_and_state())

        assert result == 1
        assert runtime.current_time == start + timedelta(seconds=10)


class TestWaitUntilWithSimulationRuntime:
    def test_wait_until_advances_to_target_time(self):
        start = datetime(2025, 1, 1, 12, 0, 0)
        target = datetime(2025, 1, 1, 14, 0, 0)

        @do
        def program_with_wait():
            yield WaitUntil(target)
            return "arrived"

        runtime = SimulationRuntime(start_time=start)
        result = runtime.run(program_with_wait())

        assert result == "arrived"
        assert runtime.current_time == target

    def test_multiple_wait_untils(self):
        start = datetime(2025, 1, 1, 12, 0, 0)
        target1 = datetime(2025, 1, 1, 13, 0, 0)
        target2 = datetime(2025, 1, 1, 15, 0, 0)

        @do
        def program_with_waits():
            yield WaitUntil(target1)
            yield WaitUntil(target2)
            return "done"

        runtime = SimulationRuntime(start_time=start)
        result = runtime.run(program_with_waits())

        assert result == "done"
        assert runtime.current_time == target2


class TestDelayWithAsyncioRuntime:
    @pytest.mark.asyncio
    async def test_delay_works_with_asyncio_runtime(self):
        @do
        def program_with_delay():
            yield Delay(seconds=0.001)
            return "done"

        runtime = AsyncioRuntime()
        result = await runtime.run(program_with_delay())

        assert result == "done"


class TestDelayWithSyncRuntime:
    def test_delay_works_with_sync_runtime(self):
        @do
        def program_with_delay():
            yield Delay(seconds=0.001)
            return "done"

        runtime = SyncRuntime()
        result = runtime.run(program_with_delay())

        assert result == "done"


class TestCombinedEffects:
    def test_delay_wait_until_combined(self):
        start = datetime(2025, 1, 1, 12, 0, 0)
        target = datetime(2025, 1, 1, 12, 30, 0)

        @do
        def combined_program():
            yield Delay(seconds=60)
            yield WaitUntil(target)
            yield Delay(seconds=120)
            return "done"

        runtime = SimulationRuntime(start_time=start)
        result = runtime.run(combined_program())

        assert result == "done"
        expected_time = target + timedelta(seconds=120)
        assert runtime.current_time == expected_time
