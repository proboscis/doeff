
from dataclasses import dataclass
from datetime import timedelta

import pytest
from doeff_core_effects.scheduler import Task, Wait
from doeff_time.effects import Delay, GetTime, ScheduleAt
from doeff_time.handlers import async_time_handler, sim_time_handler, sync_time_handler
from time_test_support import run_with_handlers

from doeff import Effect, EffectBase, Pass, Resume, do
from doeff import handler as _program_handler


def _make_marker_program(marker: dict[str, bool]):
    @do
    def _mark():
        marker["done"] = True

    return _mark()


@dataclass(frozen=True)
class _OuterHandledEffect(EffectBase):
    pass


def _make_outer_effect_program():
    @do
    def _program():
        yield _OuterHandledEffect()

    return _program()


def _outer_handler(marker: dict[str, bool]):
    @do
    def _handler(effect: Effect, k):
        if isinstance(effect, _OuterHandledEffect):
            marker["done"] = True
            return (yield Resume(k, None))
        yield Pass(effect, k)

    return _program_handler(_handler)


def test_schedule_at_executes_program_when_clock_advances() -> None:
    """ScheduleAt returns a Task. The scheduled program runs as a spawned
    task when the sim clock advances past the target time."""
    marker = {"done": False}

    @do
    def program():
        current = yield GetTime()
        task = yield ScheduleAt(current + timedelta(seconds=0.03), _make_marker_program(marker))
        yield Delay(0.1)  # advance sim clock past target
        yield Wait(task)

    run_with_handlers(
        sim_time_handler()(program()),
    )
    assert marker["done"] is True


def test_schedule_at_preserves_outer_handler_stack() -> None:
    """Scheduled program can perform effects handled by outer handlers."""
    marker = {"done": False}

    @do
    def program():
        current = yield GetTime()
        task = yield ScheduleAt(current + timedelta(seconds=0.01), _make_outer_effect_program())
        yield Delay(0.1)
        yield Wait(task)

    run_with_handlers(
        _outer_handler(marker)(sim_time_handler()(program())),
    )
    assert marker["done"] is True


# ---------------------------------------------------------------------------
# #503: async/sync ScheduleAt must return the spawned Task (like sim) so the
# caller can Wait/Gather it and observe failures of the deferred program.
# ---------------------------------------------------------------------------


class _ScheduledBoomError(Exception):
    pass


def _make_raising_program():
    @do
    def _boom():
        raise _ScheduledBoomError("deferred program failed")
        yield  # pragma: no cover - makes this a generator function

    return _boom()


def test_async_schedule_at_returns_task_and_wait_raises_deferred_failure() -> None:
    """#503 regression: ScheduleAt of a raising program under the async
    handler returns a Task whose Wait re-raises the deferred error."""

    @do
    def program():
        current = yield GetTime()
        target = current + timedelta(seconds=0.01)
        task = yield ScheduleAt(target, _make_raising_program())
        assert isinstance(task, Task)
        yield Wait(task)

    with pytest.raises(_ScheduledBoomError):
        run_with_handlers(
            async_time_handler()(program()),
        )


def test_async_schedule_at_task_is_waitable_on_success() -> None:
    marker = {"done": False}

    @do
    def program():
        current = yield GetTime()
        target = current + timedelta(seconds=0.01)
        task = yield ScheduleAt(target, _make_marker_program(marker))
        assert isinstance(task, Task)
        yield Wait(task)

    run_with_handlers(
        async_time_handler()(program()),
    )
    assert marker["done"] is True


def test_sync_schedule_at_returns_task_and_wait_raises_deferred_failure() -> None:
    """#503 regression: same contract for the sync (threading.Timer) handler."""

    @do
    def program():
        current = yield GetTime()
        target = current + timedelta(seconds=0.01)
        task = yield ScheduleAt(target, _make_raising_program())
        assert isinstance(task, Task)
        yield Wait(task)

    with pytest.raises(_ScheduledBoomError):
        run_with_handlers(
            sync_time_handler()(program()),
        )


def test_sync_schedule_at_task_is_waitable_on_success() -> None:
    marker = {"done": False}

    @do
    def program():
        current = yield GetTime()
        target = current + timedelta(seconds=0.01)
        task = yield ScheduleAt(target, _make_marker_program(marker))
        assert isinstance(task, Task)
        yield Wait(task)

    run_with_handlers(
        sync_time_handler()(program()),
    )
    assert marker["done"] is True
