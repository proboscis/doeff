from typing import Any

import pytest
from doeff_time import GetTime, poll_until, sim_time_handler

from conftest import run_with_handlers, sim_seconds, sim_time
from doeff import Ask, Pure, do
from doeff import handler as _install_raw_handler


def _run_with_sim(program: Any):
    return run_with_handlers(
        _install_raw_handler(sim_time_handler(start_time=sim_time(0.0)))(program),
    )


def test_poll_until_accepts_effectful_done() -> None:
    seen: list[float] = []

    @do
    def poll():
        now = yield GetTime()
        seen.append(sim_seconds(now))
        return len(seen)

    @do
    def done(value: int):
        threshold = yield Ask("threshold")
        return value >= threshold

    @do
    def program():
        return (
            yield poll_until(
                poll,
                done,
                deadline=sim_time(10.0),
                interval_seconds=1.0,
            )
        )

    result = run_with_handlers(
        _install_raw_handler(sim_time_handler(start_time=sim_time(0.0)))(program()),
        env={"threshold": 3},
    )

    assert result == 3
    assert seen == [0.0, 1.0, 2.0]


def test_poll_until_returns_last_value_at_deadline() -> None:
    seen: list[float] = []

    @do
    def poll():
        now = yield GetTime()
        seen.append(sim_seconds(now))
        return len(seen)

    def done(_value: int) -> bool:
        return False

    @do
    def program():
        return (
            yield poll_until(
                poll,
                done,
                deadline=sim_time(2.5),
                interval_seconds=1.0,
            )
        )

    result = _run_with_sim(program())

    assert result == 4
    assert seen == [0.0, 1.0, 2.0, 2.5]


def test_poll_until_accepts_plain_poll_and_program_done() -> None:
    values = iter([1, 2])

    def poll() -> int:
        return next(values)

    def done(value: int):
        return Pure(value == 2)

    @do
    def program():
        return (
            yield poll_until(
                poll,
                done,
                deadline=sim_time(5.0),
                interval_seconds=1.0,
            )
        )

    assert _run_with_sim(program()) == 2


def test_poll_until_rejects_non_bool_done_result() -> None:
    @do
    def program():
        return (
            yield poll_until(
                lambda: "value",
                lambda _value: "yes",
                deadline=sim_time(1.0),
                interval_seconds=1.0,
            )
        )

    with pytest.raises(TypeError, match=r"done\(value\) must return bool"):
        _run_with_sim(program())


def test_poll_until_rejects_non_positive_interval() -> None:
    with pytest.raises(ValueError, match=r"interval_seconds must be > 0\.0"):
        poll_until(
            lambda: 1,
            lambda _value: False,
            deadline=sim_time(1.0),
            interval_seconds=0.0,
        )


def test_poll_until_rejects_invalid_deadline() -> None:
    with pytest.raises(TypeError, match="deadline must be an aware datetime"):
        poll_until(
            lambda: 1,
            lambda _value: False,
            deadline="soon",
            interval_seconds=1.0,
        )
