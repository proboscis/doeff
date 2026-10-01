"""WaitWithin — a scheduler future waited for at most a duration, the deadline owned by the clock (agora-redesign #2618).

- The future's value is answered when it completes first, at the virtual time it completed.
- ``None`` is answered exactly when the duration passes first.
- Under ``sim_time_handler`` the wait spawns no task: the deadline is one entry on the virtual time
  queue, withdrawn when the future wins. The counterexample — a timed wait that spawns a sleeping
  timer task per wait (the shape promise-or-timeout had) — spawns one task per wait and fails the
  count below.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from doeff_core_effects.scheduler import CompletePromise, CreatePromise, Promise, Race, Spawn
from doeff_time import Delay, GetTime, WaitWithin
from doeff_time._internals import TimeQueue
from time_test_support import run_with_handlers, sim_time

from doeff import Effect, Pass, do
from doeff import handler as _install_raw_handler
from doeff_time import sim_time_handler


@dataclass(frozen=True)
class Counted:
    """A run on the virtual clock: how many Spawn effects reached the scheduler, and the program's answer."""

    spawns: int
    result: Any


def _spawns_and_result(program: Any) -> Counted:
    """Run ``program`` on the virtual clock, counting the Spawn effects that reach the scheduler."""
    spawns = 0

    @do
    def count_spawns(effect: Effect, k: Any):
        nonlocal spawns
        if isinstance(effect, Spawn):
            spawns += 1
        yield Pass(effect, k)

    result = run_with_handlers(_install_raw_handler(count_spawns)(sim_time_handler(start_time=sim_time(0.0))(program)))
    return Counted(spawns=spawns, result=result)


@do
def _complete_after(promise: Any, seconds: float, value: str):
    yield Delay(seconds)
    yield CompletePromise(promise, value)


def test_the_future_value_is_answered_when_it_completes_first() -> None:
    @do
    def _program():
        promise = yield CreatePromise()
        yield Spawn(_complete_after(promise, 2.0, "done"))
        value = yield WaitWithin(promise.future, 5.0)
        now = yield GetTime()
        return value, now

    assert _spawns_and_result(_program()).result == ("done", sim_time(2.0))


def test_none_is_answered_exactly_when_the_duration_passes_first() -> None:
    @do
    def _program():
        promise = yield CreatePromise()
        value = yield WaitWithin(promise.future, 5.0)
        now = yield GetTime()
        return value, now

    assert _spawns_and_result(_program()).result == (None, sim_time(5.0))


def test_timed_waits_spawn_no_task_on_the_virtual_clock() -> None:
    # Five timed waits on futures that already completed: the only Spawn is the clock's own driver.
    @do
    def _program():
        answers = []
        for n in range(5):
            promise = yield CreatePromise()
            yield CompletePromise(promise, n)
            answers = [*answers, (yield WaitWithin(promise.future, 30.0))]
        return answers

    counted = _spawns_and_result(_program())
    assert counted.result == [0, 1, 2, 3, 4]
    assert counted.spawns == 1, counted


def test_the_counterexample_timer_task_per_wait_spawns_per_wait() -> None:
    # The shape WaitWithin replaces: a sleeping timer task raced against the future. Five waits spawn
    # five timers on top of the clock driver — the count the test above forbids.
    @do
    def _expire(seconds: float):
        yield Delay(seconds)

    @do
    def _program():
        for n in range(5):
            promise = yield CreatePromise()
            yield CompletePromise(promise, n)
            timer = yield Spawn(_expire(30.0), daemon=True)
            yield Race(promise.future, timer)
        return None

    counted = _spawns_and_result(_program())
    assert counted.spawns > 1, counted


def test_a_withdrawn_entry_is_skipped_by_the_queue() -> None:
    base = sim_time(0.0)
    queue = TimeQueue()
    first = queue.push(base + timedelta(seconds=1), Promise(1))
    queue.push(base + timedelta(seconds=2), Promise(2))
    queue.withdraw(first)
    assert len(queue) == 1
    assert queue.pop().time == base + timedelta(seconds=2)
    assert queue.empty()
    queue.withdraw(first)  # already gone — no-op
    assert queue.empty()
