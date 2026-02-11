from __future__ import annotations

import time

import pytest
from doeff_time.effects import GetTime
from doeff_time.handlers import async_time_handler, sync_time_handler

from doeff import WithHandler, async_run, default_handlers, do, run


@do
def _read_time_program():
    return (yield GetTime())


def test_get_time_sync_handler_returns_current_time() -> None:
    before = time.time()
    result = run(
        WithHandler(sync_time_handler(), _read_time_program()),
        handlers=default_handlers(),
    )
    after = time.time()

    assert result.is_ok()
    assert before <= result.value <= after


@pytest.mark.asyncio
async def test_get_time_async_handler_returns_current_time() -> None:
    before = time.time()
    result = await async_run(
        WithHandler(async_time_handler(), _read_time_program()),
        handlers=default_handlers(),
    )
    after = time.time()

    assert result.is_ok()
    assert before <= result.value <= after
