import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import pytest
from doeff_core_effects import Ask
from doeff_time.effects import Delay, WaitUntil
from doeff_time.handlers import async_time_handler
from time_test_support import run_with_handlers

from doeff import do


@do
def _delay_program(seconds: float):
    yield Delay(seconds)


@do
def _wait_until_program(target: datetime):
    yield WaitUntil(target)


@do
def _delegate_probe_program():
    return (yield Ask("delegated_key"))


def test_async_delay_uses_wall_clock_sleep() -> None:
    start = time.perf_counter()
    run_with_handlers(
        async_time_handler()(_delay_program(0.03)),
    )
    elapsed = time.perf_counter() - start

    assert elapsed >= 0.025


def test_async_wait_until_blocks_until_target_time() -> None:
    target = datetime.now(timezone.utc) + timedelta(seconds=0.03)
    start = time.perf_counter()
    run_with_handlers(
        async_time_handler()(_wait_until_program(target)),
    )
    elapsed = time.perf_counter() - start

    assert elapsed >= 0.025


def test_async_handler_delegates_non_time_effects() -> None:
    result = run_with_handlers(
        async_time_handler()(_delegate_probe_program()),
        env={"delegated_key": "ok"},
    )
    assert result == "ok"


# agora-redesign #765: a Delay is a clock wait whose wake time is known, so
# the scheduler must not report it as stalled; only a clock wait that does not
# wake by its deadline (plus one stall interval) is a stall. The stall
# interval is shrunk so the 300 s wait of the report becomes 10 intervals.

_STALL_INTERVAL = 0.05


def _stall_messages(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if "scheduler stalled" in record.getMessage()
    ]


@pytest.fixture(params=["python", "rust"])
def stall_watch(request, monkeypatch, caplog):
    import doeff_core_effects.scheduler as sched_module

    monkeypatch.setenv("DOEFF_SCHEDULER", request.param)
    monkeypatch.setattr(sched_module, "EXTERNAL_STALL_LOG_INTERVAL_SECONDS", _STALL_INTERVAL)
    caplog.set_level(logging.WARNING, logger="doeff_core_effects.scheduler")
    return caplog


def test_async_delay_is_not_reported_as_stalled(stall_watch) -> None:
    run_with_handlers(async_time_handler()(_delay_program(_STALL_INTERVAL * 10)))
    assert _stall_messages(stall_watch) == []


def test_async_wait_until_is_not_reported_as_stalled(stall_watch) -> None:
    target = datetime.now(timezone.utc) + timedelta(seconds=_STALL_INTERVAL * 10)
    run_with_handlers(async_time_handler()(_wait_until_program(target)))
    assert _stall_messages(stall_watch) == []


def test_async_delay_that_oversleeps_its_deadline_is_reported(stall_watch) -> None:
    # Counterexample: the clock wait does not wake at its deadline (the sleep
    # overruns by 10 intervals), so the scheduler is stalled and must say so.
    async def oversleep(seconds: float) -> None:
        await asyncio.sleep(seconds + _STALL_INTERVAL * 10)

    run_with_handlers(async_time_handler(sleep=oversleep)(_delay_program(_STALL_INTERVAL)))
    assert _stall_messages(stall_watch), "an overdue clock wait must be reported as stalled"
