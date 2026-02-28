
import asyncio
import time

import pytest
from doeff_time.effects import GetTime, ScheduleAt
from doeff_time.handlers import async_time_handler, sync_time_handler

from doeff import WithHandler, async_run, default_handlers, do, run


def _make_marker_program(marker: dict[str, bool]):
    @do
    def _mark():
        marker["done"] = True

    return _mark()


@do
def _schedule_marker_program(marker: dict[str, bool], delay_seconds: float):
    current = yield GetTime()
    yield ScheduleAt(current + delay_seconds, _make_marker_program(marker))


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
