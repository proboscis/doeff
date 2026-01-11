"""Tests for SimAwait effect and simulation scheduling.

This tests the discrete event simulation capabilities using the
new handler protocol with continuation (k) parameter.
"""

from __future__ import annotations

import pytest
from doeff._vendor import FrozenDict
from doeff.cesk import (
    Continuation,
    CESKState,
    Value,
    Next,
    Resume,
)
from doeff.effects.sim import (
    SimAwait,
    SimAwaitEffect,
    SimTime,
    SimTimeEffect,
    SimulationScheduler,
    handle_sim_await,
    handle_sim_time,
    SIM_SCHEDULER_KEY,
    SIM_TIME_KEY,
)


# ============================================================================
# Tests: SimAwait Effect
# ============================================================================


class TestSimAwaitEffect:
    """Test SimAwaitEffect creation and validation."""

    def test_create_sim_await_with_delay(self):
        """SimAwait can be created with positive delay."""
        effect = SimAwait(delay=0.5)
        assert isinstance(effect, SimAwaitEffect)
        assert effect.delay == 0.5
        assert effect.until is None

    def test_create_sim_await_with_until(self):
        """SimAwait can be created with absolute time."""
        effect = SimAwait(until=10.5)
        assert isinstance(effect, SimAwaitEffect)
        assert effect.until == 10.5
        assert effect.delay is None

    def test_create_sim_await_zero_delay(self):
        """SimAwait can be created with zero delay."""
        effect = SimAwait(delay=0.0)
        assert effect.delay == 0.0

    def test_sim_await_negative_delay_raises(self):
        """SimAwait with negative delay raises ValueError."""
        with pytest.raises(ValueError, match="non-negative"):
            SimAwait(delay=-0.1)

    def test_sim_await_no_args_raises(self):
        """SimAwait without until or delay raises ValueError."""
        with pytest.raises(ValueError, match="requires either"):
            SimAwait()

    def test_sim_await_is_frozen(self):
        """SimAwaitEffect is immutable."""
        effect = SimAwait(delay=1.0)
        with pytest.raises(Exception):
            effect.delay = 2.0

    def test_get_target_time_with_delay(self):
        """get_target_time calculates correctly with delay."""
        effect = SimAwait(delay=0.5)
        assert effect.get_target_time(current_time=10.0) == 10.5

    def test_get_target_time_with_until(self):
        """get_target_time returns until value directly."""
        effect = SimAwait(until=25.0)
        assert effect.get_target_time(current_time=10.0) == 25.0

    def test_until_takes_precedence(self):
        """When both until and delay given, until takes precedence."""
        effect = SimAwaitEffect(until=100.0, delay=5.0)
        assert effect.get_target_time(current_time=10.0) == 100.0


# ============================================================================
# Tests: SimulationScheduler
# ============================================================================


class TestSimulationScheduler:
    """Test the priority queue-based scheduler."""

    def test_empty_scheduler(self):
        """New scheduler is empty."""
        scheduler = SimulationScheduler()
        assert scheduler.is_empty()
        assert scheduler.peek_time() is None

    def test_push_creates_new_scheduler(self):
        """Push returns a new scheduler (immutable)."""
        scheduler = SimulationScheduler()
        k = Continuation(_frames=[], _env=FrozenDict())

        new_scheduler = scheduler.push(1.0, k, {})

        assert scheduler.is_empty()  # Original unchanged
        assert not new_scheduler.is_empty()

    def test_pop_returns_earliest(self):
        """Pop returns entry with earliest time."""
        scheduler = SimulationScheduler()
        k1 = Continuation(_frames=[], _env=FrozenDict({"id": 1}))
        k2 = Continuation(_frames=[], _env=FrozenDict({"id": 2}))
        k3 = Continuation(_frames=[], _env=FrozenDict({"id": 3}))

        scheduler = scheduler.push(2.0, k2, {"name": "second"})
        scheduler = scheduler.push(1.0, k1, {"name": "first"})
        scheduler = scheduler.push(3.0, k3, {"name": "third"})

        entry, scheduler = scheduler.pop()
        assert entry.time == 1.0
        assert entry.store == {"name": "first"}

        entry, scheduler = scheduler.pop()
        assert entry.time == 2.0

        entry, scheduler = scheduler.pop()
        assert entry.time == 3.0

    def test_pop_fifo_for_same_time(self):
        """Entries with same time are popped in FIFO order."""
        scheduler = SimulationScheduler()
        k1 = Continuation(_frames=[], _env=FrozenDict())
        k2 = Continuation(_frames=[], _env=FrozenDict())
        k3 = Continuation(_frames=[], _env=FrozenDict())

        scheduler = scheduler.push(1.0, k1, {"order": 1})
        scheduler = scheduler.push(1.0, k2, {"order": 2})
        scheduler = scheduler.push(1.0, k3, {"order": 3})

        entry, scheduler = scheduler.pop()
        assert entry.store == {"order": 1}

        entry, scheduler = scheduler.pop()
        assert entry.store == {"order": 2}

        entry, scheduler = scheduler.pop()
        assert entry.store == {"order": 3}

    def test_pop_empty_raises(self):
        """Pop on empty scheduler raises IndexError."""
        scheduler = SimulationScheduler()
        with pytest.raises(IndexError):
            scheduler.pop()

    def test_peek_time(self):
        """Peek returns earliest time without removing."""
        scheduler = SimulationScheduler()
        k = Continuation(_frames=[], _env=FrozenDict())

        scheduler = scheduler.push(5.0, k, {})
        scheduler = scheduler.push(3.0, k, {})
        scheduler = scheduler.push(7.0, k, {})

        assert scheduler.peek_time() == 3.0
        assert not scheduler.is_empty()  # Not removed


# ============================================================================
# Tests: SimAwait Handler
# ============================================================================


class TestSimAwaitHandler:
    """Test handle_sim_await using new handler protocol."""

    def test_handle_sim_await_schedules_continuation(self):
        """Handler schedules continuation and switches to next."""
        # Setup: scheduler with one waiting process
        k_waiting = Continuation(_frames=[], _env=FrozenDict({"id": "waiting"}))
        scheduler = SimulationScheduler()
        scheduler = scheduler.push(0.5, k_waiting, {"process": "waiting"})

        # Current process yields SimAwait
        effect = SimAwait(delay=1.0)
        env = FrozenDict({"id": "current"})
        store = {
            SIM_SCHEDULER_KEY: scheduler,
            SIM_TIME_KEY: 0.0,
            "process": "current",
        }
        k_current = Continuation(_frames=[], _env=env)

        result = handle_sim_await(effect, env, store, k_current)

        # Should return Next with the waiting process's state
        assert isinstance(result, Next)
        assert isinstance(result.state, CESKState)
        assert isinstance(result.state.C, Value)
        assert result.state.C.v is None  # SimAwait resumes with None

        # Time should advance to waiting process's time
        assert result.state.S[SIM_TIME_KEY] == 0.5

    def test_handle_sim_await_updates_scheduler(self):
        """Handler updates scheduler in store."""
        k_other = Continuation(_frames=[], _env=FrozenDict())
        scheduler = SimulationScheduler()
        scheduler = scheduler.push(0.2, k_other, {})

        effect = SimAwait(delay=0.5)
        env = FrozenDict()
        store = {
            SIM_SCHEDULER_KEY: scheduler,
            SIM_TIME_KEY: 0.1,
        }
        k = Continuation(_frames=[], _env=env)

        result = handle_sim_await(effect, env, store, k)

        # Scheduler in result should have the current process scheduled at 0.6
        new_scheduler = result.state.S[SIM_SCHEDULER_KEY]
        assert not new_scheduler.is_empty()
        assert new_scheduler.peek_time() == 0.6  # 0.1 + 0.5

    def test_handle_sim_await_without_scheduler_raises(self):
        """Handler raises if no scheduler in store."""
        effect = SimAwait(delay=0.5)
        env = FrozenDict()
        store = {}  # No scheduler
        k = Continuation(_frames=[], _env=env)

        with pytest.raises(RuntimeError, match="simulation context"):
            handle_sim_await(effect, env, store, k)


# ============================================================================
# Tests: SimTime Handler
# ============================================================================


class TestSimTimeHandler:
    """Test handle_sim_time."""

    def test_handle_sim_time_returns_current_time(self):
        """Handler returns current simulation time."""
        effect = SimTime()
        env = FrozenDict()
        store = {SIM_TIME_KEY: 42.5}
        k = Continuation(_frames=[], _env=env)

        result = handle_sim_time(effect, env, store, k)

        assert isinstance(result, Resume)
        assert result.value == 42.5
        assert result.store == store

    def test_handle_sim_time_defaults_to_zero(self):
        """Handler returns 0.0 if no time in store."""
        effect = SimTime()
        env = FrozenDict()
        store = {}
        k = Continuation(_frames=[], _env=env)

        result = handle_sim_time(effect, env, store, k)

        assert isinstance(result, Resume)
        assert result.value == 0.0
