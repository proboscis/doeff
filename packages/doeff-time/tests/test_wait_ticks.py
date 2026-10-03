"""WaitTicks — a future waited for across a run of ticks, without waking the waiter at each tick (agora-redesign #3066).

- The answer is ``TicksOutcome(value, passed)``: the future's value and the ticks that passed before it, or
  ``TicksOutcome(None, count)`` once the last tick passes.
- At a tick instant the order against the other timers is that of a waiter re-registering
  ``WaitWithin(future, every)`` at each tick (the per-tick wait the sim host's sleep used to be): a timer
  registered before tick k-1 passed comes before tick k, one registered after it comes after.
- Under ``sim_time_handler`` the waiter is not woken at the ticks: one wait is one Race however many ticks
  pass. The counterexamples — the per-tick loop (a Race per tick) and one plain WaitWithin over the whole run
  (the order at a tick instant is lost) — fail the checks below.
"""

import threading
from dataclasses import dataclass
from typing import Any

from doeff_core_effects.scheduler import (
    CompletePromise,
    CreatePromise,
    Future,
    Promise,
    Race,
    Spawn,
    scheduled,
)
from doeff_time import Delay, GetTime, TicksOutcome, WaitTicks, WaitWithin, sim_time_handler
from doeff_time.handlers.sync_time import sync_time_handler
from time_test_support import run_with_handlers, sim_time

from doeff import Effect, Pass, do
from doeff import handler as _install_raw_handler
from doeff import run as doeff_run

EVERY = 10.0


@dataclass(frozen=True)
class Raced:
    """A run on the virtual clock: how many Race effects reached the scheduler, and the program's answer."""

    races: int
    result: Any


def _races_and_result(program: Any) -> Raced:
    """Run ``program`` on the virtual clock, counting the Race effects that reach the scheduler."""
    races = 0

    @do
    def count_races(effect: Effect, k: Any):
        nonlocal races
        if isinstance(effect, Race):
            races += 1
        yield Pass(effect, k)

    result = run_with_handlers(
        _install_raw_handler(count_races)(sim_time_handler(start_time=sim_time(0.0))(program))
    )
    return Raced(races=races, result=result)


@do
def _per_tick(future: "Future[str]", every: float, count: int):
    """The per-tick wait WaitTicks stands for: WaitWithin re-registered at each tick."""
    passed = 0
    while passed < count:
        value = yield WaitWithin(future, every)
        if value is not None:
            return TicksOutcome(value=value, passed=passed)
        passed += 1
    return TicksOutcome(value=None, passed=count)


@do
def _one_plain_wait(future: "Future[str]", every: float, count: int):
    """Counterexample: one WaitWithin over the whole run, the ticks counted from the time it was answered."""
    started = yield GetTime()
    value = yield WaitWithin(future, every * count)
    now = yield GetTime()
    passed = count if value is None else int((now - started).total_seconds() // every)
    return TicksOutcome(value=value, passed=passed)


@do
def _complete_at(promise: "Promise[str]", registered_at: float, due_at: float):
    """Register (at ``registered_at`` seconds) a timer due at ``due_at`` seconds that completes ``promise``."""
    yield Delay(registered_at)
    yield Delay(due_at - registered_at)
    yield CompletePromise(promise, "rung")


def _at_a_tick_instant(wait: Any, registered_at: float) -> TicksOutcome:
    """Wait (``wait``) for a future completed exactly at tick 2 (20 s) by a timer registered at ``registered_at``."""

    @do
    def _program():
        promise = yield CreatePromise()
        yield Spawn(_complete_at(promise, registered_at, 2 * EVERY))
        return (yield wait(promise.future, EVERY, 5))

    return _races_and_result(_program()).result


def test_the_future_value_is_answered_with_the_ticks_before_it() -> None:
    @do
    def _program():
        promise = yield CreatePromise()
        yield Spawn(_complete_at(promise, 0.0, 25.0))
        outcome = yield WaitTicks(promise.future, EVERY, 5)
        now = yield GetTime()
        return outcome, now

    assert _races_and_result(_program()).result == (
        TicksOutcome(value="rung", passed=2),
        sim_time(25.0),
    )


def test_none_is_answered_when_the_last_tick_passes() -> None:
    @do
    def _program():
        promise = yield CreatePromise()
        outcome = yield WaitTicks(promise.future, EVERY, 3)
        now = yield GetTime()
        return outcome, now

    assert _races_and_result(_program()).result == (
        TicksOutcome(value=None, passed=3),
        sim_time(30.0),
    )


def test_a_tick_instant_orders_like_the_per_tick_wait() -> None:
    # Registered before tick 1 passed (5 s): the future comes first at 20 s — one tick passed.
    # Registered after tick 1 passed (15 s): tick 2 comes first — two ticks passed.
    for registered_at, passed in ((5.0, 1), (15.0, 2)):
        reference = _at_a_tick_instant(_per_tick, registered_at)
        assert reference == TicksOutcome(value="rung", passed=passed), (registered_at, reference)
        assert _at_a_tick_instant(WaitTicks, registered_at) == reference, registered_at


def test_the_counterexample_one_plain_wait_loses_the_order_at_a_tick_instant() -> None:
    # One WaitWithin over the run has no tick-1 registration to order against: both futures look
    # the same (20 s after the start), so one of the two cases differs from the per-tick wait.
    plain = [_at_a_tick_instant(_one_plain_wait, registered_at) for registered_at in (5.0, 15.0)]
    reference = [_at_a_tick_instant(_per_tick, registered_at) for registered_at in (5.0, 15.0)]
    assert plain != reference, (plain, reference)


def test_the_waiter_is_not_woken_at_the_ticks() -> None:
    # A hundred ticks pass: WaitTicks is one Race; the per-tick wait (the counterexample) is a Race per tick.
    @do
    def _waiting(wait: Any):
        promise = yield CreatePromise()
        return (yield wait(promise.future, 1.0, 100))

    ticks = _races_and_result(_waiting(WaitTicks))
    assert ticks.result == TicksOutcome(value=None, passed=100)
    assert ticks.races == 1, ticks.races
    per_tick = _races_and_result(_waiting(_per_tick))
    assert per_tick.result == ticks.result
    assert per_tick.races == 100, per_tick.races


def test_a_future_that_wins_withdraws_the_queued_tick() -> None:
    # The future completes before the first tick: the clock does not advance to the withdrawn tick.
    @do
    def _program():
        promise = yield CreatePromise()
        yield CompletePromise(promise, "already")
        outcome = yield WaitTicks(promise.future, EVERY, 4)
        yield Delay(1.0)
        now = yield GetTime()
        return outcome, now

    assert _races_and_result(_program()).result == (
        TicksOutcome(value="already", passed=0),
        sim_time(1.0),
    )


def test_the_wall_clock_answers_the_same_meaning() -> None:
    # The sync wall clock: the ticks run out (all passed), and a future completed at once (none passed).
    result: dict[str, Any] = {}

    @do
    def _program():
        quiet = yield CreatePromise()
        ran_out = yield WaitTicks(quiet.future, 0.02, 3)
        done = yield CreatePromise()
        yield CompletePromise(done, "now")
        at_once = yield WaitTicks(done.future, 5.0, 3)
        return ran_out, at_once

    def _worker() -> None:
        result["value"] = doeff_run(scheduled(sync_time_handler()(_program())))

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=5.0)
    assert not thread.is_alive(), "wall-clock wait hung"
    assert result["value"] == (
        TicksOutcome(value=None, passed=3),
        TicksOutcome(value="now", passed=0),
    )
