import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest
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


# Delay / WaitUntil against the wall clock and the pass-through of non-time
# effects are the shared contract of every clock handler:
# test_time_contract.hy (agora-redesign #1159). This file keeps what only the
# async handler has — its clock waits as seen by the scheduler's stall report.

# agora-redesign #765: a Delay is a clock wait whose wake time is known, so
# the scheduler must not report it as stalled; only a clock wait that does not
# wake by its deadline (plus one stall interval) is a stall. The stall
# interval is shrunk so the 300 s wait of the report becomes 10 intervals.

_STALL_INTERVAL = 0.05


class _OnStallReport(logging.Handler):
    """Calls ``on_report`` when the scheduler logs a stall (from any thread)."""

    def __init__(self, on_report) -> None:
        super().__init__(level=logging.WARNING)
        self._on_report = on_report

    def emit(self, record: logging.LogRecord) -> None:
        if "scheduler stalled" in record.getMessage():
            self._on_report()


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
    # Counterexample: the clock wait does not wake at its deadline — it wakes
    # only when the scheduler reports the stall (no fixed oversleep), so the
    # scheduler must say so.  If it never does, the guard ends the wait with
    # TimeoutError and the run fails.
    async def wake_on_stall_report(seconds: float) -> None:
        loop = asyncio.get_running_loop()
        reported = asyncio.Event()
        watcher = _OnStallReport(lambda: loop.call_soon_threadsafe(reported.set))
        logging.getLogger("doeff_core_effects.scheduler").addHandler(watcher)
        try:
            await asyncio.wait_for(reported.wait(), timeout=seconds + _STALL_INTERVAL * 40)
        finally:
            logging.getLogger("doeff_core_effects.scheduler").removeHandler(watcher)

    run_with_handlers(async_time_handler(sleep=wake_on_stall_report)(_delay_program(_STALL_INTERVAL)))
    assert _stall_messages(stall_watch), "an overdue clock wait must be reported as stalled"
