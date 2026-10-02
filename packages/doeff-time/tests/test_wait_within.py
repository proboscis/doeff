"""WaitWithin — a scheduler future waited for at most a duration, the deadline owned by the clock (agora-redesign #2618).

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
from doeff_core_effects.scheduler import (
    PRIORITY_NORMAL,
    CompletePromise,
    CreateExternalPromise,
    CreatePromise,
    Promise,
    Race,
    Spawn,
)
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


# --- park: 外の promise を、仮想の時計を止めずに待つ(agora-redesign #3054)-----------------------------------------
# 外の promise(handler の外の同期の書きや別の thread が埋める — doeff-records の memory の置き場の呼び鈴)の既定の待ちは、
# 仮想の時計の駆動を止める(埋まる前に時間を進めない)。期限つきの待ちでは期限まで時計が進む必要があるので、park で待つ。


def _complete_from_a_thread(promise: Any, value: str) -> None:
    """外の promise を、実の時計で 0.2 秒後に別の thread から埋めるため(run の外からの完了)。"""
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


def test_a_parked_wait_on_an_external_promise_reaches_its_deadline() -> None:
    # park: 外の promise が埋まる前に、仮想の時計が期限(5 秒)へ進み None が返る。task は時計の駆動の 1 本だけ。
    counted = _spawns_and_result(_wait_external_within(True))
    assert counted.result == (None, sim_time(5.0)), counted
    assert counted.spawns == 1, counted


def test_the_counterexample_unparked_wait_holds_the_clock_until_the_promise_completes() -> None:
    # park しない既定の待ちは、外の promise が埋まるまで仮想の時計を止める — 期限の 5 秒は来ず、実の時計の 0.2 秒後の完了が
    # 仮想の 0 秒で返る(期限つきの待ちが期限を持たない形 — park が要る理由)。
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


def test_a_parked_wait_answers_a_completion_made_by_in_run_code() -> None:
    # run の中の task が仮想の 2 秒後に外の promise を埋めると、park した待ちはその刻に値で返る(期限の 5 秒を待たない)。
    assert _spawns_and_result(_in_run_completion_within()).result == ("rung", sim_time(2.0))


def test_park_is_a_bool_and_race_takes_only_the_park_mode() -> None:
    with pytest.raises(TypeError):
        WaitWithin(Promise(1).future, 1.0, park=1)  # type: ignore[arg-type]  # 型の外の値を断る事を確かめる
    with pytest.raises(ValueError, match="park mode"):
        Race(Promise(1).future, priority=PRIORITY_NORMAL)
