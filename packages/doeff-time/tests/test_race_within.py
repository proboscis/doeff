"""RaceWithin — several scheduler futures raced against a deadline the clock owns.

- The value of the first future to complete is answered with ``timed_out`` false — even a None.
- ``timed_out`` is true exactly when the duration passes first.
- A failed future is raised, as Race raises it.
- Under ``sim_time_handler`` the race spawns no task: the deadline is one entry on the virtual time queue,
  withdrawn when a future wins (the shape it replaces — a sleeping timer task per wait — is
  test_wait_within.py's counterexample).
"""

from typing import Any

import pytest
from doeff_core_effects.scheduler import CompletePromise, CreatePromise, FailPromise, Spawn
from doeff_time import Delay, GetTime, RaceOutcome, RaceWithin, sim_time_handler
from time_test_support import run_with_handlers, sim_time

from doeff import Effect, Pass, do
from doeff import handler as _install_raw_handler


class _BrokenError(Exception):
    pass


def _spawns_and_result(program: Any) -> tuple[int, Any]:
    """Run ``program`` on the virtual clock: the Spawn effects that reached the scheduler, and its answer."""
    spawns = 0

    @do
    def count_spawns(effect: Effect, k: Any):
        nonlocal spawns
        if isinstance(effect, Spawn):
            spawns += 1
        yield Pass(effect, k)

    result = run_with_handlers(
        _install_raw_handler(count_spawns)(sim_time_handler(start_time=sim_time(0.0))(program))
    )
    return spawns, result


def test_the_first_future_value_is_answered_at_the_time_it_completed() -> None:
    @do
    def _program():
        pending = yield CreatePromise()
        bell = yield CreatePromise()
        yield Spawn(_complete_after(bell, 2.0, "rung"))
        outcome = yield RaceWithin((pending.future, bell.future), 5.0)
        now = yield GetTime()
        return outcome, now

    assert _spawns_and_result(_program())[1] == (
        RaceOutcome(value="rung", timed_out=False),
        sim_time(2.0),
    )


def test_timed_out_is_answered_exactly_when_the_duration_passes_first() -> None:
    @do
    def _program():
        pending = yield CreatePromise()
        outcome = yield RaceWithin((pending.future,), 5.0)
        now = yield GetTime()
        return outcome, now

    assert _spawns_and_result(_program())[1] == (
        RaceOutcome(value=None, timed_out=True),
        sim_time(5.0),
    )


def test_a_failed_future_is_raised() -> None:
    @do
    def _program():
        broken = yield CreatePromise()
        yield FailPromise(broken, _BrokenError("broken"))
        yield RaceWithin((broken.future,), 5.0)

    with pytest.raises(_BrokenError):
        _spawns_and_result(_program())


def test_races_spawn_no_task_on_the_virtual_clock() -> None:
    # Five races whose bell already rang (some with None): the only Spawn is the clock's own driver, and
    # the withdrawn deadlines do not move the clock.
    @do
    def _program():
        answers = []
        for n in range(5):
            pending = yield CreatePromise()
            bell = yield CreatePromise()
            yield CompletePromise(bell, n if n % 2 else None)
            answers = [*answers, (yield RaceWithin((pending.future, bell.future), 30.0))]
        yield Delay(1.0)
        now = yield GetTime()
        return answers, now

    spawns, (answers, now) = _spawns_and_result(_program())
    assert answers == [RaceOutcome(value=n if n % 2 else None, timed_out=False) for n in range(5)]
    assert now == sim_time(1.0)
    assert spawns == 1, spawns


def test_futures_must_be_a_non_empty_tuple() -> None:
    with pytest.raises(ValueError, match="at least one"):
        RaceWithin((), 1.0)
    with pytest.raises(TypeError, match="tuple"):
        RaceWithin([object()], 1.0)  # type: ignore[arg-type]  # the check under test: a list is refused


@do
def _complete_after(promise: Any, seconds: float, value: str):
    yield Delay(seconds)
    yield CompletePromise(promise, value)
