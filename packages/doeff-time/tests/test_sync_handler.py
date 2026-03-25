
import time
from datetime import datetime, timedelta, timezone

from doeff_time.effects import Delay, WaitUntil
from doeff_time.handlers import sync_time_handler

from conftest import run_with_handlers
from doeff import WithHandler, do
from doeff_core_effects import Ask


@do
def _delay_program(seconds: float):
    yield Delay(seconds)


@do
def _wait_until_program(target: datetime):
    yield WaitUntil(target)


@do
def _delegate_probe_program():
    return (yield Ask("delegated_key"))


def test_sync_delay_uses_wall_clock_sleep() -> None:
    start = time.perf_counter()
    run_with_handlers(
        WithHandler(sync_time_handler(), _delay_program(0.03)),
    )
    elapsed = time.perf_counter() - start

    assert elapsed >= 0.025


def test_sync_wait_until_blocks_until_target_time() -> None:
    target = datetime.now(timezone.utc) + timedelta(seconds=0.03)
    start = time.perf_counter()
    run_with_handlers(
        WithHandler(sync_time_handler(), _wait_until_program(target)),
    )
    elapsed = time.perf_counter() - start

    assert elapsed >= 0.025


def test_sync_handler_delegates_non_time_effects() -> None:
    result = run_with_handlers(
        WithHandler(sync_time_handler(), _delegate_probe_program()),
        env={"delegated_key": "ok"},
    )
    assert result == "ok"
