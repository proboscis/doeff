
import asyncio
import time
from dataclasses import dataclass
from datetime import timedelta

import pytest
from doeff_time.effects import GetTime, ScheduleAt
from doeff_time.handlers import async_time_handler, sync_time_handler

from doeff import EffectBase, Pass, Resume, WithHandler, async_run, default_handlers, do, run
from doeff.effects.base import Effect


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
        yield Pass()

    return _handler


@do
def _schedule_marker_program(marker: dict[str, bool], delay_seconds: float):
    current = yield GetTime()
    yield ScheduleAt(current + timedelta(seconds=delay_seconds), _make_marker_program(marker))


def test_schedule_at_sync_executes_program_without_blocking_caller() -> None:
    marker = {"done": False}
    start = time.perf_counter()
    result = run(
        WithHandler(sync_time_handler(), _schedule_marker_program(marker, 0.03)),
        handlers=default_handlers(),
    )
    elapsed = time.perf_counter() - start

    assert result.is_ok()
    assert elapsed < 0.03

    time.sleep(0.08)
    assert marker["done"] is True


@pytest.mark.asyncio
async def test_schedule_at_async_executes_program_without_blocking_caller() -> None:
    marker = {"done": False}
    start = time.perf_counter()
    result = await async_run(
        WithHandler(async_time_handler(), _schedule_marker_program(marker, 0.03)),
        handlers=default_handlers(),
    )
    elapsed = time.perf_counter() - start

    assert result.is_ok()
    assert elapsed < 0.03

    await asyncio.sleep(0.08)
    assert marker["done"] is True


def test_schedule_at_sync_preserves_outer_handler_stack() -> None:
    marker = {"done": False}

    @do
    def program():
        current = yield GetTime()
        yield ScheduleAt(current + timedelta(seconds=0.01), _make_outer_effect_program())

    result = run(
        WithHandler(
            _outer_handler(marker),
            WithHandler(sync_time_handler(), program()),
        ),
        handlers=default_handlers(),
    )

    assert result.is_ok()

    time.sleep(0.08)
    assert marker["done"] is True


@pytest.mark.asyncio
async def test_schedule_at_async_preserves_outer_handler_stack() -> None:
    marker = {"done": False}

    @do
    def program():
        current = yield GetTime()
        yield ScheduleAt(current + timedelta(seconds=0.01), _make_outer_effect_program())

    result = await async_run(
        WithHandler(
            _outer_handler(marker),
            WithHandler(async_time_handler(), program()),
        ),
        handlers=default_handlers(),
    )

    assert result.is_ok()

    await asyncio.sleep(0.08)
    assert marker["done"] is True
