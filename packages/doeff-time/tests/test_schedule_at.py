
from dataclasses import dataclass
from datetime import timedelta

from doeff_time.effects import Delay, GetTime, ScheduleAt
from doeff_time.handlers import sim_time_handler

from conftest import run_with_handlers
from doeff import Effect, EffectBase, Pass, Resume, WithHandler, do
from doeff_core_effects.scheduler import Wait


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

    return _handler


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
        WithHandler(sim_time_handler(), program()),
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
        WithHandler(
            _outer_handler(marker),
            WithHandler(sim_time_handler(), program()),
        ),
    )
    assert marker["done"] is True
