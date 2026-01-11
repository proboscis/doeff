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
    ScheduledHandlerRegistry,
    SimDelay,
    SimSubmit,
    SimulationScheduler,
    SimWaitUntil,
    Suspend,
    adapt_async_handler,
    adapt_pure_handler,
    create_sim_delay_handler,
    create_sim_submit_handler,
    run_with_scheduler,
    run_with_scheduler_sync,
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


class TestScheduledHandlerRegistry:
    def test_register_and_lookup(self):
        from doeff.effects.state import StateGetEffect
        
        def handler(effect, env, store, k, scheduler):
            return Resume(42, store)
        
        registry = ScheduledHandlerRegistry()
        registry.register(StateGetEffect, handler)
        
        effect = StateGetEffect(key="test")
        found = registry.lookup(effect)
        
        assert found is handler

    def test_mro_lookup(self):
        from doeff._types_internal import EffectBase
        from doeff.effects.state import StateGetEffect, StatePutEffect
        
        def handler(effect, env, store, k, scheduler):
            return Resume("base", store)
        
        registry = ScheduledHandlerRegistry()
        registry.register(EffectBase, handler)
        
        get_effect = StateGetEffect(key="test")
        found = registry.lookup(get_effect)
        
        assert found is handler

    def test_not_found_returns_none(self):
        from doeff.effects.state import StateGetEffect
        
        registry = ScheduledHandlerRegistry()
        effect = StateGetEffect(key="test")
        
        assert registry.lookup(effect) is None


class TestAdaptPureHandler:
    def test_adapt_pure_handler_returns_resume(self):
        def pure_handler(effect, env, store):
            return (42, {**store, "handled": True})
        
        adapted = adapt_pure_handler(pure_handler)
        
        from doeff._vendor import FrozenDict
        from doeff.cesk import CESKState, Value
        from doeff.effects.state import StateGetEffect
        
        env = FrozenDict()
        store = {}
        k = Continuation(
            _resume=lambda v, s: CESKState(C=Value(v), E=env, S=s, K=[]),
            _resume_error=lambda ex: CESKState(C=Value(-1), E=env, S={}, K=[]),
            env=env,
            store=store,
        )
        scheduler = FIFOScheduler()
        
        result = adapted(StateGetEffect(key="test"), env, store, k, scheduler)
        
        assert isinstance(result, Resume)
        assert result.value == 42
        assert result.store == {"handled": True}


@pytest.mark.asyncio
async def test_adapt_async_handler_returns_suspend():
    async def async_handler(effect, env, store):
        await asyncio.sleep(0)
        return (123, {**store, "async": True})
    
    adapted = adapt_async_handler(async_handler)
    
    from doeff._vendor import FrozenDict
    from doeff.cesk import CESKState, Value
    from doeff.effects.state import StateGetEffect
    
    env = FrozenDict()
    store = {}
    k = Continuation(
        _resume=lambda v, s: CESKState(C=Value(v), E=env, S=s, K=[]),
        _resume_error=lambda ex: CESKState(C=Value(-1), E=env, S={}, K=[]),
        env=env,
        store=store,
    )
    scheduler = FIFOScheduler()
    
    result = adapted(StateGetEffect(key="test"), env, store, k, scheduler)
    
    assert isinstance(result, Suspend)


class TestSimulationEffects:
    def test_sim_delay_dataclass(self):
        delay = SimDelay(seconds=1.5)
        assert delay.seconds == 1.5

    def test_sim_wait_until_dataclass(self):
        target = datetime(2025, 1, 1, 12, 0, 0)
        effect = SimWaitUntil(target_time=target)
        assert effect.target_time == target

    def test_sim_submit_dataclass(self):
        from doeff.program import Program
        
        program = Program.pure(42)
        effect = SimSubmit(program=program)
        
        assert effect.program is program
        assert effect.daemon is False
        
        daemon_effect = SimSubmit(program=program, daemon=True)
        assert daemon_effect.daemon is True


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
    
    @do
    def simple_program():
        yield Put("x", 10)
        x = yield Get("x")
        return x * 2
    
    from doeff.runtime import adapt_pure_handler
    from doeff.cesk import _handle_state_get, _handle_state_put
    from doeff.effects import StateGetEffect, StatePutEffect
    
    handlers = ScheduledHandlerRegistry()
    handlers.register(StateGetEffect, adapt_pure_handler(_handle_state_get))
    handlers.register(StatePutEffect, adapt_pure_handler(_handle_state_put))
    
    scheduler = FIFOScheduler()
    result = await run_with_scheduler(simple_program(), scheduler, handlers)
    
    assert result == 20


def test_run_with_scheduler_sync():
    from doeff.do import do
    from doeff.effects import Get, Put
    
    @do
    def simple_program():
        yield Put("x", 5)
        x = yield Get("x")
        return x + 1
    
    from doeff.cesk import _handle_state_get, _handle_state_put
    from doeff.effects import StateGetEffect, StatePutEffect
    
    handlers = ScheduledHandlerRegistry()
    handlers.register(StateGetEffect, adapt_pure_handler(_handle_state_get))
    handlers.register(StatePutEffect, adapt_pure_handler(_handle_state_put))
    
    scheduler = FIFOScheduler()
    result = run_with_scheduler_sync(simple_program(), scheduler, handlers)
    
    assert result == 6
