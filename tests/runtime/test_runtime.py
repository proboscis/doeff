"""Tests for the new runtime module: Single-shot Algebraic Effects with Pluggable Scheduler."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from doeff.runtime import (
    AwaitPayload,
    Continuation,
    DelayPayload,
    FIFOScheduler,
    HandlerResult,
    Pending,
    PriorityScheduler,
    Ready,
    RealtimeScheduler,
    Resume,
    Schedule,
    Scheduled,
    ScheduledEffectHandler,
    SchedulerItem,
    SimDelay,
    SimSubmit,
    SimulationScheduler,
    SimWaitUntil,
    SpawnPayload,
    Suspend,
    WaitUntilPayload,
    create_sim_delay_handler,
    create_sim_submit_handler,
    create_sim_wait_until_handler,
)


class TestHandlerResults:
    def test_resume_holds_value_and_store(self):
        store = {"key": "value"}
        result = Resume(42, store)
        assert result.value == 42
        assert result.store == store

    def test_schedule_holds_payload_and_store(self):
        store = {"key": "value"}
        payload = DelayPayload(timedelta(seconds=5))
        result = Schedule(payload, store)
        assert result.payload == payload
        assert result.store == store


class TestSchedulerResultTypes:
    def test_ready_holds_value(self):
        result = Ready(42)
        assert result.value == 42

    def test_pending_holds_awaitable(self):
        async def some_async():
            return 123
        coro = some_async()
        result = Pending(coro)
        assert result.awaitable is coro
        coro.close()

    def test_scheduler_item_holds_all(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        
        env = FrozenDict()
        store = {"key": "value"}
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value(v), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value(-1), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        item = SchedulerItem(k, Ready(42), store)
        assert item.k is k
        assert isinstance(item.result, Ready)
        assert item.store == store


class TestPayloadTypes:
    def test_await_payload(self):
        async def some_async():
            return 123
        coro = some_async()
        payload = AwaitPayload(coro)
        assert payload.awaitable is coro
        coro.close()

    def test_delay_payload(self):
        duration = timedelta(seconds=5)
        payload = DelayPayload(duration)
        assert payload.duration == duration

    def test_wait_until_payload(self):
        target = datetime(2025, 1, 1, 12, 0, 0)
        payload = WaitUntilPayload(target)
        assert payload.target == target

    def test_spawn_payload(self):
        from doeff._vendor import FrozenDict
        from doeff.program import Program
        
        program = Program.pure(42)
        env = FrozenDict()
        store = {}
        payload = SpawnPayload(program, env, store)
        assert payload.program is program
        assert payload.env == env
        assert payload.store == store


class TestFIFOScheduler:
    def test_empty_scheduler_returns_none(self):
        scheduler = FIFOScheduler()
        assert scheduler.next() is None

    def test_fifo_order(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        
        scheduler = FIFOScheduler()
        env = FrozenDict()
        store = {}
        
        def make_k(val):
            return Continuation(
                _resume=lambda v, s, val=val: CESKState(C=Value(val), E=env, S=s, K=[]),
                _resume_error=lambda ex: CESKState(C=Value(-1), E=env, S={}, K=[]),
                env=env,
                store=store,
            )
        
        k1, k2, k3 = make_k(1), make_k(2), make_k(3)
        
        scheduler.submit(k1, AwaitPayload(asyncio.sleep(0)), store)
        scheduler.submit(k2, AwaitPayload(asyncio.sleep(0)), store)
        scheduler.submit(k3, AwaitPayload(asyncio.sleep(0)), store)
        
        assert len(scheduler) == 3
        
        item1 = scheduler.next()
        assert item1.k.resume(None, store).C.v == 1
        
        item2 = scheduler.next()
        assert item2.k.resume(None, store).C.v == 2
        
        item3 = scheduler.next()
        assert item3.k.resume(None, store).C.v == 3
        
        assert scheduler.next() is None

    def test_await_payload_returns_pending(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        
        scheduler = FIFOScheduler()
        env = FrozenDict()
        store = {}
        
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value(1), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value(-1), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        scheduler.submit(k, AwaitPayload(asyncio.sleep(0)), store)
        item = scheduler.next()
        assert isinstance(item.result, Pending)

    def test_delay_payload_returns_pending(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        
        scheduler = FIFOScheduler()
        env = FrozenDict()
        store = {}
        
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value(1), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value(-1), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        scheduler.submit(k, DelayPayload(timedelta(seconds=1)), store)
        item = scheduler.next()
        assert isinstance(item.result, Pending)


class TestPriorityScheduler:
    def test_fifo_when_same_priority(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        
        scheduler = PriorityScheduler()
        env = FrozenDict()
        store = {}
        
        def make_k(val):
            return Continuation(
                _resume=lambda v, s, val=val: CESKState(C=Value(val), E=env, S=s, K=[]),
                _resume_error=lambda ex: CESKState(C=Value(-1), E=env, S={}, K=[]),
                env=env,
                store=store,
            )
        
        scheduler.submit(make_k(1), AwaitPayload(asyncio.sleep(0)), store)
        scheduler.submit(make_k(2), AwaitPayload(asyncio.sleep(0)), store)
        scheduler.submit(make_k(3), AwaitPayload(asyncio.sleep(0)), store)
        
        item1 = scheduler.next()
        assert item1.k.resume(None, store).C.v == 1
        
        item2 = scheduler.next()
        assert item2.k.resume(None, store).C.v == 2
        
        item3 = scheduler.next()
        assert item3.k.resume(None, store).C.v == 3


class TestSimulationScheduler:
    def test_ready_after_spawn(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        from doeff.program import Program
        
        scheduler = SimulationScheduler()
        env = FrozenDict()
        store = {}
        
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value("parent"), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value(-1), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        child_program = Program.pure(42)
        scheduler.submit(k, SpawnPayload(child_program, env, store), store)
        
        item1 = scheduler.next()
        assert isinstance(item1.result, Ready)
        
        item2 = scheduler.next()
        assert isinstance(item2.result, Ready)

    def test_timed_scheduling_returns_ready(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        
        start = datetime(2025, 1, 1, 12, 0, 0)
        scheduler = SimulationScheduler(start_time=start)
        env = FrozenDict()
        store = {}
        
        def make_k(val):
            return Continuation(
                _resume=lambda v, s, val=val: CESKState(C=Value(val), E=env, S=s, K=[]),
                _resume_error=lambda ex: CESKState(C=Value(-1), E=env, S={}, K=[]),
                env=env,
                store=store,
            )
        
        scheduler.submit(make_k(1), DelayPayload(timedelta(seconds=1)), store)
        
        item = scheduler.next()
        assert isinstance(item.result, Ready)
        assert scheduler.current_time == start + timedelta(seconds=1)

    def test_await_payload_returns_pending(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        
        scheduler = SimulationScheduler()
        env = FrozenDict()
        store = {}
        
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value(1), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value(-1), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        scheduler.submit(k, AwaitPayload(asyncio.sleep(0)), store)
        item = scheduler.next()
        assert isinstance(item.result, Pending)


class TestContinuation:
    def test_single_shot_enforcement(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        
        env = FrozenDict()
        store = {}
        
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value(v), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value(-1), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        k.resume(42, store)
        
        with pytest.raises(RuntimeError, match="already used"):
            k.resume(43, store)

    def test_single_shot_for_resume_error(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        
        env = FrozenDict()
        store = {}
        
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value(v), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value(-1), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        k.resume_error(ValueError("test"), store)
        
        with pytest.raises(RuntimeError, match="already used"):
            k.resume(42, store)

    def test_from_program_creates_continuation(self):
        from doeff._vendor import FrozenDict
        from doeff.program import Program
        
        env = FrozenDict({"key": "value"})
        store = {"state": 123}
        program = Program.pure(42)
        
        k = Continuation.from_program(program, env, store)
        
        assert k.env == env
        assert k.store == store
        assert not k._used


class TestSimulationEffects:
    def test_sim_delay_is_effect_base(self):
        from doeff._types_internal import EffectBase
        
        delay = SimDelay(seconds=1.5)
        assert isinstance(delay, EffectBase)
        assert delay.seconds == 1.5
        assert delay.created_at is None

    def test_sim_delay_intercept_returns_self(self):
        delay = SimDelay(seconds=1.5)
        intercepted = delay.intercept(lambda e: e)
        assert intercepted is delay

    def test_sim_wait_until_is_effect_base(self):
        from doeff._types_internal import EffectBase
        
        target = datetime(2025, 1, 1, 12, 0, 0)
        effect = SimWaitUntil(target_time=target)
        assert isinstance(effect, EffectBase)
        assert effect.target_time == target

    def test_sim_wait_until_intercept_returns_self(self):
        target = datetime(2025, 1, 1, 12, 0, 0)
        effect = SimWaitUntil(target_time=target)
        intercepted = effect.intercept(lambda e: e)
        assert intercepted is effect

    def test_sim_submit_is_effect_base(self):
        from doeff._types_internal import EffectBase
        from doeff.program import Program
        
        program = Program.pure(42)
        effect = SimSubmit(program=program)
        
        assert isinstance(effect, EffectBase)
        assert effect.program is program
        assert effect.daemon is False
        
        daemon_effect = SimSubmit(program=program, daemon=True)
        assert daemon_effect.daemon is True


class TestSimDelayHandler:
    def test_handler_returns_schedule_with_delay_payload(self):
        from doeff._vendor import FrozenDict
        
        env = FrozenDict()
        store = {}
        
        handler = create_sim_delay_handler()
        effect = SimDelay(seconds=5.0)
        
        result = handler(effect, env, store)
        
        assert isinstance(result, Schedule)
        assert isinstance(result.payload, DelayPayload)
        assert result.payload.duration == timedelta(seconds=5.0)
        assert result.store == store


class TestSimWaitUntilHandler:
    def test_handler_returns_schedule_with_wait_until_payload(self):
        from doeff._vendor import FrozenDict
        
        env = FrozenDict()
        store = {}
        target = datetime(2025, 1, 1, 12, 0, 0)
        
        handler = create_sim_wait_until_handler()
        effect = SimWaitUntil(target_time=target)
        
        result = handler(effect, env, store)
        
        assert isinstance(result, Schedule)
        assert isinstance(result.payload, WaitUntilPayload)
        assert result.payload.target == target
        assert result.store == store


class TestSimSubmitHandler:
    def test_handler_returns_schedule_with_spawn_payload(self):
        from doeff._vendor import FrozenDict
        from doeff.program import Program
        
        env = FrozenDict()
        store = {}
        
        handler = create_sim_submit_handler()
        submitted_program = Program.pure(42)
        effect = SimSubmit(program=submitted_program)
        
        result = handler(effect, env, store)
        
        assert isinstance(result, Schedule)
        assert isinstance(result.payload, SpawnPayload)
        assert result.payload.program is submitted_program
        assert result.store == store


class TestRealtimeScheduler:
    def test_await_payload_returns_pending(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        
        scheduler = RealtimeScheduler()
        env = FrozenDict()
        store = {}
        
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value(1), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value(-1), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        scheduler.submit(k, AwaitPayload(asyncio.sleep(0)), store)
        item = scheduler.next()
        assert isinstance(item.result, Pending)

    def test_delay_payload_returns_pending(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        
        scheduler = RealtimeScheduler()
        env = FrozenDict()
        store = {}
        
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value(1), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value(-1), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        scheduler.submit(k, DelayPayload(timedelta(seconds=0.01)), store)
        item = scheduler.next()
        assert isinstance(item.result, Pending)


@pytest.mark.asyncio
async def test_run_with_scheduler_pure_program():
    from doeff.do import do
    from doeff.effects import Get, Put
    from doeff.cesk import run

    @do
    def simple_program():
        yield Put("x", 10)
        x = yield Get("x")
        return x * 2

    scheduler = FIFOScheduler()
    result = await run(simple_program(), scheduler=scheduler)

    assert result.value == 20


def test_run_with_scheduler_sync():
    from doeff.do import do
    from doeff.effects import Get, Put
    from doeff.cesk import run_sync

    @do
    def simple_program():
        yield Put("x", 5)
        x = yield Get("x")
        return x + 1

    scheduler = FIFOScheduler()
    result = run_sync(simple_program(), scheduler=scheduler)

    assert result.value == 6


class TestSimulationEffectsIntegration:
    @pytest.mark.asyncio
    async def test_sim_delay_runs_end_to_end(self):
        from doeff.do import do
        from doeff.cesk import run
        from doeff.runtime import create_sim_delay_handler
        
        start = datetime(2025, 1, 1, 12, 0, 0)
        
        @do
        def program_with_delay():
            yield SimDelay(seconds=10.0)
            return "done"
        
        scheduler = SimulationScheduler(start_time=start)
        handlers = {SimDelay: create_sim_delay_handler()}
        
        result = await run(
            program_with_delay(),
            scheduler=scheduler,
            scheduled_handlers=handlers,
        )
        
        assert result.value == "done"
        assert scheduler.current_time == start + timedelta(seconds=10)

    @pytest.mark.asyncio
    async def test_sim_wait_until_runs_end_to_end(self):
        from doeff.do import do
        from doeff.cesk import run
        from doeff.runtime import create_sim_wait_until_handler
        
        start = datetime(2025, 1, 1, 12, 0, 0)
        target = datetime(2025, 1, 1, 13, 0, 0)
        
        @do
        def program_with_wait():
            yield SimWaitUntil(target_time=target)
            return "arrived"
        
        scheduler = SimulationScheduler(start_time=start)
        handlers = {SimWaitUntil: create_sim_wait_until_handler()}
        
        result = await run(
            program_with_wait(),
            scheduler=scheduler,
            scheduled_handlers=handlers,
        )
        
        assert result.value == "arrived"
        assert scheduler.current_time == target

    @pytest.mark.asyncio
    async def test_sim_submit_schedules_child_program(self):
        from doeff.do import do
        from doeff.cesk import run
        from doeff.runtime import create_sim_submit_handler
        
        @do
        def child_program():
            return "child_result"
        
        @do
        def main_program():
            yield SimSubmit(program=child_program())
            return "main_done"
        
        scheduler = FIFOScheduler()
        handlers = {SimSubmit: create_sim_submit_handler()}
        
        result = await run(
            main_program(),
            scheduler=scheduler,
            scheduled_handlers=handlers,
        )
        
        assert result.value == "main_done"

    @pytest.mark.asyncio
    async def test_multiple_delays_in_sequence(self):
        from doeff.do import do
        from doeff.cesk import run
        from doeff.runtime import create_sim_delay_handler
        
        start = datetime(2025, 1, 1, 12, 0, 0)
        checkpoints = []
        
        @do
        def multi_delay_program():
            checkpoints.append(("start", start))
            yield SimDelay(seconds=5.0)
            checkpoints.append(("after_5s", start + timedelta(seconds=5)))
            yield SimDelay(seconds=10.0)
            checkpoints.append(("after_15s", start + timedelta(seconds=15)))
            return len(checkpoints)
        
        scheduler = SimulationScheduler(start_time=start)
        handlers = {SimDelay: create_sim_delay_handler()}
        
        result = await run(
            multi_delay_program(),
            scheduler=scheduler,
            scheduled_handlers=handlers,
        )
        
        assert result.value == 3
        assert scheduler.current_time == start + timedelta(seconds=15)
