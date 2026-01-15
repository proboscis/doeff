"""Tests for unified time effects: Delay, WaitUntil, Spawn across all runtimes."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from doeff.do import do
from doeff.effects import Delay, DelayEffect, Get, Put, Spawn, WaitUntil, WaitUntilEffect
from doeff.runtime import (
    FIFOScheduler,
    RealtimeScheduler,
    SimulationScheduler,
)
from doeff.cesk import run
from doeff.scheduled_handlers import default_scheduled_handlers


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


class TestDelayWithSimulationScheduler:
    @pytest.mark.asyncio
    async def test_delay_advances_simulation_time(self):
        start = datetime(2025, 1, 1, 12, 0, 0)

        @do
        def program_with_delay():
            yield Delay(seconds=10.0)
            return "done"

        scheduler = SimulationScheduler(start_time=start)
        handlers = default_scheduled_handlers()

        result = await run(
            program_with_delay(),
            scheduler=scheduler,
            scheduled_handlers=handlers,
        )

        assert result.value == "done"
        assert scheduler.current_time == start + timedelta(seconds=10)

    @pytest.mark.asyncio
    async def test_multiple_delays_accumulate_time(self):
        start = datetime(2025, 1, 1, 12, 0, 0)

        @do
        def program_with_delays():
            yield Delay(seconds=5.0)
            yield Delay(seconds=10.0)
            yield Delay(seconds=3.0)
            return "done"

        scheduler = SimulationScheduler(start_time=start)
        handlers = default_scheduled_handlers()

        result = await run(
            program_with_delays(),
            scheduler=scheduler,
            scheduled_handlers=handlers,
        )

        assert result.value == "done"
        assert scheduler.current_time == start + timedelta(seconds=18)

    @pytest.mark.asyncio
    async def test_delay_with_state_effects(self):
        start = datetime(2025, 1, 1, 12, 0, 0)

        @do
        def program_with_delay_and_state():
            yield Put("counter", 0)
            yield Delay(seconds=5.0)
            count = yield Get("counter")
            yield Put("counter", count + 1)
            yield Delay(seconds=5.0)
            return (yield Get("counter"))

        scheduler = SimulationScheduler(start_time=start)
        handlers = default_scheduled_handlers()

        result = await run(
            program_with_delay_and_state(),
            scheduler=scheduler,
            scheduled_handlers=handlers,
        )

        assert result.value == 1
        assert scheduler.current_time == start + timedelta(seconds=10)


class TestWaitUntilWithSimulationScheduler:
    @pytest.mark.asyncio
    async def test_wait_until_advances_to_target_time(self):
        start = datetime(2025, 1, 1, 12, 0, 0)
        target = datetime(2025, 1, 1, 14, 0, 0)

        @do
        def program_with_wait():
            yield WaitUntil(target)
            return "arrived"

        scheduler = SimulationScheduler(start_time=start)
        handlers = default_scheduled_handlers()

        result = await run(
            program_with_wait(),
            scheduler=scheduler,
            scheduled_handlers=handlers,
        )

        assert result.value == "arrived"
        assert scheduler.current_time == target

    @pytest.mark.asyncio
    async def test_multiple_wait_untils(self):
        start = datetime(2025, 1, 1, 12, 0, 0)
        target1 = datetime(2025, 1, 1, 13, 0, 0)
        target2 = datetime(2025, 1, 1, 15, 0, 0)

        @do
        def program_with_waits():
            yield WaitUntil(target1)
            yield WaitUntil(target2)
            return "done"

        scheduler = SimulationScheduler(start_time=start)
        handlers = default_scheduled_handlers()

        result = await run(
            program_with_waits(),
            scheduler=scheduler,
            scheduled_handlers=handlers,
        )

        assert result.value == "done"
        assert scheduler.current_time == target2


class TestDelayWithFIFOScheduler:
    @pytest.mark.asyncio
    async def test_delay_works_with_fifo_scheduler(self):
        @do
        def program_with_delay():
            yield Delay(seconds=0.001)
            return "done"

        scheduler = FIFOScheduler()
        handlers = default_scheduled_handlers()

        result = await run(
            program_with_delay(),
            scheduler=scheduler,
            scheduled_handlers=handlers,
        )

        assert result.value == "done"


class TestDelayWithRealtimeScheduler:
    @pytest.mark.asyncio
    async def test_delay_works_with_realtime_scheduler(self):
        @do
        def program_with_delay():
            yield Delay(seconds=0.001)
            return "done"

        scheduler = RealtimeScheduler()
        handlers = default_scheduled_handlers()

        result = await run(
            program_with_delay(),
            scheduler=scheduler,
            scheduled_handlers=handlers,
        )

        assert result.value == "done"


class TestSpawnWithSimulationScheduler:
    @pytest.mark.asyncio
    async def test_spawn_works_with_simulation_scheduler(self):
        @do
        def child_program():
            return "child_result"

        @do
        def main_program():
            task = yield Spawn(child_program())
            result = yield task.join()
            return result

        scheduler = SimulationScheduler()
        handlers = default_scheduled_handlers()

        result = await run(
            main_program(),
            scheduler=scheduler,
            scheduled_handlers=handlers,
        )

        assert result.value == "child_result"

    @pytest.mark.asyncio
    async def test_spawn_with_delay_in_child(self):
        start = datetime(2025, 1, 1, 12, 0, 0)

        @do
        def child_with_delay():
            yield Delay(seconds=5.0)
            return "child_done"

        @do
        def main_program():
            task = yield Spawn(child_with_delay())
            result = yield task.join()
            return result

        scheduler = SimulationScheduler(start_time=start)
        handlers = default_scheduled_handlers()

        result = await run(
            main_program(),
            scheduler=scheduler,
            scheduled_handlers=handlers,
        )

        assert result.value == "child_done"


class TestSpawnWithFIFOScheduler:
    @pytest.mark.asyncio
    async def test_spawn_works_with_fifo_scheduler(self):
        @do
        def child_program():
            return "child_result"

        @do
        def main_program():
            task = yield Spawn(child_program())
            result = yield task.join()
            return result

        scheduler = FIFOScheduler()
        handlers = default_scheduled_handlers()

        result = await run(
            main_program(),
            scheduler=scheduler,
            scheduled_handlers=handlers,
        )

        assert result.value == "child_result"


class TestCombinedEffects:
    @pytest.mark.asyncio
    async def test_delay_wait_until_combined(self):
        start = datetime(2025, 1, 1, 12, 0, 0)
        target = datetime(2025, 1, 1, 12, 30, 0)

        @do
        def combined_program():
            yield Delay(seconds=60)
            yield WaitUntil(target)
            yield Delay(seconds=120)
            return "done"

        scheduler = SimulationScheduler(start_time=start)
        handlers = default_scheduled_handlers()

        result = await run(
            combined_program(),
            scheduler=scheduler,
            scheduled_handlers=handlers,
        )

        assert result.value == "done"
        expected_time = target + timedelta(seconds=120)
        assert scheduler.current_time == expected_time

    @pytest.mark.asyncio
    async def test_spawn_delay_interleaved(self):
        start = datetime(2025, 1, 1, 12, 0, 0)
        events = []

        @do
        def child_program():
            events.append("child_start")
            yield Delay(seconds=5.0)
            events.append("child_end")
            return "child"

        @do
        def main_program():
            events.append("main_start")
            task = yield Spawn(child_program())
            yield Delay(seconds=2.0)
            events.append("main_after_delay")
            result = yield task.join()
            events.append("main_end")
            return result

        scheduler = SimulationScheduler(start_time=start)
        handlers = default_scheduled_handlers()

        result = await run(
            main_program(),
            scheduler=scheduler,
            scheduled_handlers=handlers,
        )

        assert result.value == "child"
        assert "main_start" in events
        assert "child_start" in events
        assert "main_end" in events


class TestDeprecationWarnings:
    def test_sim_delay_emits_warning(self):
        from doeff.runtime import SimDelay
        with pytest.warns(DeprecationWarning, match="SimDelay is deprecated"):
            SimDelay(seconds=1.0)

    def test_sim_wait_until_emits_warning(self):
        from doeff.runtime import SimWaitUntil
        with pytest.warns(DeprecationWarning, match="SimWaitUntil is deprecated"):
            SimWaitUntil(target_time=datetime.now())

    def test_sim_submit_emits_warning(self):
        from doeff.runtime import SimSubmit
        from doeff.program import Program
        with pytest.warns(DeprecationWarning, match="SimSubmit is deprecated"):
            SimSubmit(program=Program.pure(42))
