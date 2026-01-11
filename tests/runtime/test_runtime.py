"""Tests for the new runtime module: Single-shot Algebraic Effects with Pluggable Scheduler."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from doeff.runtime import (
    Continuation,
    FIFOScheduler,
    HandlerResult,
    PriorityScheduler,
    RealtimeScheduler,
    Resume,
    Scheduled,
    ScheduledEffectHandler,
    SimDelay,
    SimSubmit,
    SimulationScheduler,
    SimWaitUntil,
    Suspend,
    create_sim_delay_handler,
    create_sim_submit_handler,
)


class TestHandlerResults:
    def test_resume_holds_value_and_store(self):
        store = {"key": "value"}
        result = Resume(42, store)
        assert result.value == 42
        assert result.store == store

    def test_suspend_holds_awaitable_and_store(self):
        async def some_async():
            return 123
        
        store = {"key": "value"}
        result = Suspend(some_async(), store)
        assert result.store == store

    def test_scheduled_holds_store(self):
        store = {"key": "value"}
        result = Scheduled(store)
        assert result.store == store


class TestFIFOScheduler:
    def test_empty_scheduler_returns_none(self):
        scheduler = FIFOScheduler()
        assert scheduler.next() is None

    def test_fifo_order(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, ProgramControl, Value
        from doeff.program import Program
        
        scheduler = FIFOScheduler()
        
        env = FrozenDict()
        store = {}
        
        k1 = Continuation(
            _resume=lambda v, s: CESKState(C=Value(1), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value(-1), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        k2 = Continuation(
            _resume=lambda v, s: CESKState(C=Value(2), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value(-2), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        k3 = Continuation(
            _resume=lambda v, s: CESKState(C=Value(3), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value(-3), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        scheduler.submit(k1)
        scheduler.submit(k2)
        scheduler.submit(k3)
        
        assert len(scheduler) == 3
        
        result1 = scheduler.next()
        state1 = result1.resume(None, store)
        assert state1.C.v == 1
        
        result2 = scheduler.next()
        state2 = result2.resume(None, store)
        assert state2.C.v == 2
        
        result3 = scheduler.next()
        state3 = result3.resume(None, store)
        assert state3.C.v == 3
        
        assert scheduler.next() is None

    def test_hint_is_ignored(self):
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
        
        scheduler.submit(k, hint="ignored")
        assert len(scheduler) == 1


class TestPriorityScheduler:
    def test_priority_order(self):
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
        
        scheduler.submit(make_k(3), hint=3)
        scheduler.submit(make_k(1), hint=1)
        scheduler.submit(make_k(2), hint=2)
        
        result1 = scheduler.next()
        assert result1.resume(None, store).C.v == 1
        
        result2 = scheduler.next()
        assert result2.resume(None, store).C.v == 2
        
        result3 = scheduler.next()
        assert result3.resume(None, store).C.v == 3

    def test_default_priority_is_zero(self):
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
        
        scheduler.submit(make_k(1), hint=10)
        scheduler.submit(make_k(2))
        
        result = scheduler.next()
        assert result.resume(None, store).C.v == 2


class TestSimulationScheduler:
    def test_ready_is_lifo(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        
        scheduler = SimulationScheduler()
        env = FrozenDict()
        store = {}
        
        def make_k(val):
            return Continuation(
                _resume=lambda v, s, val=val: CESKState(C=Value(val), E=env, S=s, K=[]),
                _resume_error=lambda ex: CESKState(C=Value(-1), E=env, S={}, K=[]),
                env=env,
                store=store,
            )
        
        scheduler.submit(make_k(1))
        scheduler.submit(make_k(2))
        scheduler.submit(make_k(3))
        
        assert scheduler.next().resume(None, store).C.v == 3
        assert scheduler.next().resume(None, store).C.v == 2
        assert scheduler.next().resume(None, store).C.v == 1

    def test_timed_scheduling(self):
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
        
        scheduler.submit(make_k(3), hint=start + timedelta(seconds=3))
        scheduler.submit(make_k(1), hint=start + timedelta(seconds=1))
        scheduler.submit(make_k(2), hint=start + timedelta(seconds=2))
        
        result1 = scheduler.next()
        assert result1.resume(None, store).C.v == 1
        assert scheduler.current_time == start + timedelta(seconds=1)
        
        result2 = scheduler.next()
        assert result2.resume(None, store).C.v == 2
        assert scheduler.current_time == start + timedelta(seconds=2)
        
        result3 = scheduler.next()
        assert result3.resume(None, store).C.v == 3
        assert scheduler.current_time == start + timedelta(seconds=3)

    def test_ready_before_timed(self):
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
        
        scheduler.submit(make_k("timed"), hint=start + timedelta(seconds=1))
        scheduler.submit(make_k("ready"))
        
        assert scheduler.next().resume(None, store).C.v == "ready"
        assert scheduler.current_time == start
        
        assert scheduler.next().resume(None, store).C.v == "timed"
        assert scheduler.current_time == start + timedelta(seconds=1)

    def test_timedelta_hint(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        
        start = datetime(2025, 1, 1, 12, 0, 0)
        scheduler = SimulationScheduler(start_time=start)
        env = FrozenDict()
        store = {}
        
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value(1), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value(-1), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        scheduler.submit(k, hint=timedelta(seconds=5))
        
        result = scheduler.next()
        assert result is not None
        assert scheduler.current_time == start + timedelta(seconds=5)


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

    def test_sim_submit_intercept_transforms_program(self):
        from doeff.program import Program
        
        inner = Program.pure(42)
        effect = SimSubmit(program=inner)
        
        transform_called = []
        def transform(e):
            transform_called.append(e)
            return e
        
        intercepted = effect.intercept(transform)
        assert intercepted is effect


class TestSimDelayHandler:
    def test_handler_with_simulation_scheduler(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        
        start = datetime(2025, 1, 1, 12, 0, 0)
        scheduler = SimulationScheduler(start_time=start)
        env = FrozenDict()
        store = {}
        
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value("resumed"), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value("error"), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        handler = create_sim_delay_handler()
        effect = SimDelay(seconds=5.0)
        
        result = handler(effect, env, store, k, scheduler)
        
        assert isinstance(result, Scheduled)
        assert len(scheduler) == 1
        
        next_k = scheduler.next()
        assert scheduler.current_time == start + timedelta(seconds=5)
        assert next_k.resume(None, store).C.v == "resumed"

    def test_handler_with_realtime_scheduler_returns_suspend(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        
        scheduler = RealtimeScheduler()
        env = FrozenDict()
        store = {}
        
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value("resumed"), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value("error"), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        handler = create_sim_delay_handler()
        effect = SimDelay(seconds=0.01)
        
        result = handler(effect, env, store, k, scheduler)
        
        assert isinstance(result, Suspend)
        assert result.store == store

    def test_handler_with_negative_delay(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        
        start = datetime(2025, 1, 1, 12, 0, 0)
        scheduler = SimulationScheduler(start_time=start)
        env = FrozenDict()
        store = {}
        
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value("resumed"), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value("error"), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        handler = create_sim_delay_handler()
        effect = SimDelay(seconds=-5.0)
        
        result = handler(effect, env, store, k, scheduler)
        
        assert isinstance(result, Scheduled)
        next_k = scheduler.next()
        assert scheduler.current_time == start + timedelta(seconds=-5)


class TestSimWaitUntilHandler:
    def test_handler_with_simulation_scheduler(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        from doeff.runtime import create_sim_wait_until_handler
        
        start = datetime(2025, 1, 1, 12, 0, 0)
        target = datetime(2025, 1, 1, 12, 5, 0)
        scheduler = SimulationScheduler(start_time=start)
        env = FrozenDict()
        store = {}
        
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value("resumed"), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value("error"), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        handler = create_sim_wait_until_handler()
        effect = SimWaitUntil(target_time=target)
        
        result = handler(effect, env, store, k, scheduler)
        
        assert isinstance(result, Scheduled)
        assert len(scheduler) == 1
        
        next_k = scheduler.next()
        assert scheduler.current_time == target
        assert next_k.resume(None, store).C.v == "resumed"

    def test_handler_with_past_target_schedules_immediately(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        from doeff.runtime import create_sim_wait_until_handler
        
        start = datetime(2025, 1, 1, 12, 0, 0)
        past_target = datetime(2025, 1, 1, 11, 0, 0)
        scheduler = SimulationScheduler(start_time=start)
        env = FrozenDict()
        store = {}
        
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value("resumed"), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value("error"), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        handler = create_sim_wait_until_handler()
        effect = SimWaitUntil(target_time=past_target)
        
        result = handler(effect, env, store, k, scheduler)
        
        assert isinstance(result, Scheduled)
        assert len(scheduler) == 1
        
        next_k = scheduler.next()
        assert scheduler.current_time == start

    def test_handler_with_realtime_scheduler_future_returns_suspend(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        from doeff.runtime import create_sim_wait_until_handler
        
        scheduler = RealtimeScheduler()
        env = FrozenDict()
        store = {}
        future_target = datetime.now() + timedelta(seconds=10)
        
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value("resumed"), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value("error"), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        handler = create_sim_wait_until_handler()
        effect = SimWaitUntil(target_time=future_target)
        
        result = handler(effect, env, store, k, scheduler)
        
        assert isinstance(result, Suspend)
        assert result.store == store

    def test_handler_with_realtime_scheduler_past_returns_resume(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        from doeff.runtime import create_sim_wait_until_handler
        
        scheduler = RealtimeScheduler()
        env = FrozenDict()
        store = {}
        past_target = datetime.now() - timedelta(seconds=10)
        
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value("resumed"), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value("error"), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        handler = create_sim_wait_until_handler()
        effect = SimWaitUntil(target_time=past_target)
        
        result = handler(effect, env, store, k, scheduler)
        
        assert isinstance(result, Resume)
        assert result.value is None
        assert result.store == store


class TestSimSubmitHandler:
    def test_handler_creates_new_continuation(self):
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        from doeff.program import Program
        
        scheduler = FIFOScheduler()
        env = FrozenDict()
        store = {}
        
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value("current"), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value("error"), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        
        handler = create_sim_submit_handler()
        submitted_program = Program.pure(42)
        effect = SimSubmit(program=submitted_program)
        
        result = handler(effect, env, store, k, scheduler)
        
        assert isinstance(result, Resume)
        assert result.value is None
        assert len(scheduler) == 1


class TestRealtimeScheduler:
    def test_immediate_submit(self):
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
        
        scheduler.submit(k)
        assert len(scheduler) == 1
        
        result = scheduler.next()
        assert result is k

    @pytest.mark.asyncio
    async def test_delayed_submit(self):
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
        
        scheduler.submit(k, hint=0.01)
        
        assert len(scheduler) == 0
        
        await asyncio.sleep(0.02)
        
        assert len(scheduler) == 1


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
        assert len(scheduler) == 1

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
