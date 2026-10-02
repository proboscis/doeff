"""memory の置き場の WatchChanges の待ちは、待ち 1 回に task を作らない(#3054)。

待ちの期限は doeff-time の期限つきの待ち WaitWithin の 1 つで、仮想の時計の下では時計の列の 1 項(task を作らない)。前の形は
待ち 1 回に task を 2 本作った — 期限の鳴らしの ScheduleAt(仮想の時計が task を 1 本作る)と、起きた後にその鳴らしを取り消して
終わりを待つ片付けの Spawn。使い手の模擬では、この 2 本が筋書き 1 回で約 210 本になり、1 本ごとの作り・待ち・取り消しの往復が
scheduler の歩を使っていた。

確かめること: 待つ名に書きの来ない待ちを 3 回続けると、置き場の handler から時計の handler へ出る task の作り(Spawn と
ScheduleAt)は 0 で、答えと刻は前と同じ(timeout の刻に空の Changes)。失敗ケース = 前の形の待ちに差し替えると、同じ答えのまま
待ち 1 回に 2 本と数えられる(答えでは見分けられない欠けを、この数が見分ける)。
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import hy  # noqa: F401  Hy の module を読むため
import pytest
from doeff_core_effects.scheduler import (
    PRIORITY_IDLE,
    Cancel,
    ExternalPromise,
    Spawn,
    Task,
    TaskCancelledError,
    Wait,
    scheduled,
)
from doeff_records import memory
from doeff_records.effects import ListRows, WatchChanges
from doeff_records.laws import LAW_SCHEMA, MAKER
from doeff_records.memory import MemoryStore, memory_records_handler
from doeff_records.values import Changes, WatchCursor
from doeff_time import GetTime, ScheduleAt, ScheduleAtEffect, SimClock, sim_time_handler

from doeff import Effect, Pass, do, run, with_handlers
from doeff import handler as _install_raw_handler

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
QUIET_WAITS = 3
WAIT_SECONDS = 5.0


@dataclass(frozen=True)
class Counted:
    """仮想の時計で回した 1 回: 置き場の handler から時計の handler へ出た task の作りの数と、program の答え。"""

    task_makers: int
    result: Any


def _count_task_makers(store: MemoryStore, program: Any) -> Counted:
    """置き場の handler と時計の handler の間で Spawn と ScheduleAt を数えながら program を回すため(時計の駆動の Spawn は時計の
    handler 自身が出すので数えない — 数えるのは置き場の待ちが作る task だけ)。"""
    makers = 0

    @do
    def count_makers(effect: Effect, k: Any):
        nonlocal makers
        if isinstance(effect, (Spawn, ScheduleAtEffect)):
            makers += 1
        yield Pass(effect, k)

    inner = with_handlers([memory_records_handler(store, MAKER)], program)
    result = run(scheduled(with_handlers([sim_time_handler(clock=SimClock())], _install_raw_handler(count_makers)(inner))))
    return Counted(task_makers=makers, result=result)


@do
def _quiet_waits():
    """書きの来ない parts を WAIT_SECONDS 秒ずつ QUIET_WAITS 回待つため。答え = (最後の答え, 待ち終えた刻)。"""
    start = yield ListRows("parts")
    answer = None
    for _ in range(QUIET_WAITS):
        answer = yield WatchChanges(("parts",), WatchCursor(start.epoch, start.sequence), timeout=WAIT_SECONDS)
    now = yield GetTime()
    return answer, now


def _assert_quiet_answer(result: Any) -> None:
    """待ちの答えが前と同じ(timeout の刻に空の Changes)なことを確かめるため。"""
    answer, now = result
    assert isinstance(answer, Changes) and answer.items == (), answer
    assert now == EPOCH + timedelta(seconds=QUIET_WAITS * WAIT_SECONDS), now


def test_a_quiet_wait_makes_no_task() -> None:
    counted = _count_task_makers(MemoryStore(LAW_SCHEMA), _quiet_waits())
    _assert_quiet_answer(counted.result)
    assert counted.task_makers == 0, counted


# --- 失敗ケース: 前の形の待ち(期限の鳴らしの task と、その片付けの task)----------------------------------------------


@do
def _ring(bell: ExternalPromise) -> None:
    """期限の刻に呼び鈴を鳴らすため(前の形の ScheduleAt が回した中身)。"""
    bell.complete(None)


@do
def _withdraw(timer: Task) -> Any:
    """期限の鳴らしの task を取り消し、解け終わるまで待つため(前の形の片付けの task の中身)。"""
    yield Cancel(timer)
    try:
        yield Wait(timer)
    except TaskCancelledError:
        return None
    return None


@do
def _two_task_bell_or_timer(store: MemoryStore, bell: ExternalPromise, seconds: float) -> Any:
    """前の形の待ち: 期限の鳴らしを ScheduleAt の task にし、起きた後にその取り消しを別の task で待つため。"""
    now = yield GetTime()
    timer = yield ScheduleAt(now + timedelta(seconds=seconds), _ring(bell))
    try:
        yield Wait(bell.future, priority=PRIORITY_IDLE)
    finally:
        yield memory.drop_bell(store, bell)
        withdrawing = yield Spawn(_withdraw(timer))
        yield Wait(withdrawing)
    return None


def test_the_counterexample_two_task_wait_is_counted_per_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    broken: Callable[..., Any] = _two_task_bell_or_timer
    monkeypatch.setattr(memory, "bell_or_timer", broken)
    counted = _count_task_makers(MemoryStore(LAW_SCHEMA), _quiet_waits())
    _assert_quiet_answer(counted.result)
    assert counted.task_makers == 2 * QUIET_WAITS, counted
