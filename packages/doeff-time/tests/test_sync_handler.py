
import time
from datetime import datetime, timedelta, timezone

from doeff_time.effects import Delay, WaitUntil
from doeff_time.handlers import sync_time_handler

from doeff import WithHandler, default_handlers, do, run
from doeff.effects import ask


@do
def _delay_program(seconds: float):
    yield Delay(seconds)


@do
def _wait_until_program(target: datetime):
    yield WaitUntil(target)


@do
def _delegate_probe_program():
    return (yield ask("delegated_key"))


def test_sync_delay_uses_wall_clock_sleep() -> None:
    start = time.perf_counter()
    result = run(
        WithHandler(sync_time_handler(), _delay_program(0.03)),
        handlers=default_handlers(),
    )
    elapsed = time.perf_counter() - start

    assert result.is_ok()
    assert elapsed >= 0.025


def test_sync_wait_until_blocks_until_target_time() -> None:
    target = datetime.now(timezone.utc) + timedelta(seconds=0.03)
    start = time.perf_counter()
    result = run(
        WithHandler(sync_time_handler(), _wait_until_program(target)),
        handlers=default_handlers(),
    )
    elapsed = time.perf_counter() - start

    assert result.is_ok()
    assert elapsed >= 0.025


def test_sync_handler_delegates_non_time_effects() -> None:
    result = run(
        WithHandler(sync_time_handler(), _delegate_probe_program()),
        handlers=default_handlers(),
        env={"delegated_key": "ok"},
    )
    assert result.is_ok()
    assert result.value == "ok"
