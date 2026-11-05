"""WaitWithin — a scheduler future waited for at most a duration, the deadline owned by the clock.

- The future's value is answered when it completes first, at the virtual time it completed.
- ``None`` is answered exactly when the duration passes first.
- Under ``sim_time_handler`` the wait spawns no task: the deadline is one entry on the virtual time
  queue, withdrawn when the future wins. The counterexample — a timed wait that spawns a sleeping
  timer task per wait (the shape promise-or-timeout had) — spawns one task per wait and fails the
  count below.
"""

import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from doeff_core_effects import (
    CompletePromise,
    CreateExternalPromise,
    CreatePromise,
    Promise,
    Race,
    Spawn,
    TaskPriority,
)
from doeff_time import Delay, GetTime, WaitWithin, sim_time_handler
from doeff_time._internals import TimeQueue
from time_test_support import run_with_handlers, sim_time

from doeff import Effect, Pass, do
from doeff import handler as _install_raw_handler


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


# --- park: wait for an external promise without stopping the virtual clock -----------------------------------------
# The default wait on an external promise (filled by synchronous code outside the handler or another thread -- the doorbell of the memory store of doeff-records)
# stops the driving of the virtual clock (time does not advance before it is filled). A wait with a deadline needs the clock to advance up to the deadline, so it waits with park.
# park is read by the scheduler Race priority=TaskPriority.IDLE, so run on both versions (the rust version did not read it, and the clock stopped).


@pytest.fixture(params=["python", "rust"])
def scheduler_implementation(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """To run the park tests on both scheduler versions (run_with_handlers runs on the version of $DOEFF_SCHEDULER)."""
    monkeypatch.setenv("DOEFF_SCHEDULER", request.param)
    return request.param


def _complete_from_a_thread(promise: Any, value: str) -> None:
    """To fill an external promise from another thread after 0.2 seconds of the real clock (completion from outside run)."""
    timer = threading.Timer(0.2, lambda: promise.complete(value))
    timer.daemon = True
    timer.start()


@do
def _wait_external_within(park: bool):
    promise = yield CreateExternalPromise()
    _complete_from_a_thread(promise, "rung")
    value = yield WaitWithin(promise.future, 5.0, park=park)
    now = yield GetTime()
    return value, now


@pytest.mark.usefixtures("scheduler_implementation")
def test_a_parked_wait_on_an_external_promise_reaches_its_deadline() -> None:
    # park: before the external promise is filled, the virtual clock advances to the deadline (5 seconds) and None is returned. There is only 1 task, the one driving the clock.
    counted = _spawns_and_result(_wait_external_within(True))
    assert counted.result == (None, sim_time(5.0)), counted
    assert counted.spawns == 1, counted


@do
def _wait_never_completed_external_within():
    promise = yield CreateExternalPromise()
    value = yield WaitWithin(promise.future, 5.0, park=True)
    now = yield GetTime()
    return value, now


@pytest.mark.usefixtures("scheduler_implementation")
def test_a_parked_wait_on_an_external_promise_nobody_completes_answers_none_at_its_deadline() -> None:
    # Waiting with park on an external promise nobody fills, the virtual clock advances to the deadline and None is returned (it does not wait for the real clock).
    assert _spawns_and_result(_wait_never_completed_external_within()).result == (None, sim_time(5.0))


@pytest.mark.usefixtures("scheduler_implementation")
def test_the_counterexample_unparked_wait_holds_the_clock_until_the_promise_completes() -> None:
    # The default wait without park stops the virtual clock until the external promise is filled -- the 5 second deadline never comes, and the completion after 0.2 seconds of the real clock
    # returns at virtual 0 seconds (the form in which a wait with a deadline has no deadline -- the reason park is needed).
    assert _spawns_and_result(_wait_external_within(False)).result == ("rung", sim_time(0.0))


@do
def _in_run_completion_within():
    promise = yield CreateExternalPromise()

    @do
    def _ring_later():
        yield Delay(2.0)
        promise.complete("rung")

    yield Spawn(_ring_later())
    value = yield WaitWithin(promise.future, 5.0, park=True)
    now = yield GetTime()
    return value, now


@pytest.mark.usefixtures("scheduler_implementation")
def test_a_parked_wait_answers_a_completion_made_by_in_run_code() -> None:
    # When a task inside run fills the external promise at virtual 2 seconds, the parked wait returns with the value at that time (it does not wait for the 5 second deadline).
    assert _spawns_and_result(_in_run_completion_within()).result == ("rung", sim_time(2.0))


def test_park_is_a_bool_and_race_takes_only_the_park_mode() -> None:
    with pytest.raises(TypeError):
        WaitWithin(Promise(1).future, 1.0, park=1)  # type: ignore[arg-type]  # checks that values outside the type are rejected
    with pytest.raises(ValueError, match="park mode"):
        Race(Promise(1).future, priority=TaskPriority.NORMAL)
