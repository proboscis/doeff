from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff_events.effects import Publish, WaitForEvent
from doeff_events.handlers import event_handler
from doeff_time.effects import Delay, GetTime, ScheduleAt, SetTime, WaitUntil
from doeff_time.handlers import sim_time_handler

from doeff import (
    GatherEffect,
    Listen,
    Pass,
    Spawn,
    SpawnEffect,
    Task,
    Wait,
    WithHandler,
    default_handlers,
    do,
    gather,
    run,
)


@do
def _time_after_delay(start_delay: float):
    before = yield GetTime()
    yield Delay(start_delay)
    after = yield GetTime()
    return before, after


@do
def _time_after_wait_until(first_target: float, second_target: float):
    yield WaitUntil(first_target)
    first = yield GetTime()
    yield WaitUntil(second_target)
    second = yield GetTime()
    return first, second


def test_delay_advances_virtual_clock() -> None:
    result = run(
        WithHandler(sim_time_handler(start_time=100.0), _time_after_delay(5.0)),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value == (100.0, 105.0)


def test_wait_until_advances_virtual_clock() -> None:
    result = run(
        WithHandler(
            sim_time_handler(start_time=100.0),
            _time_after_wait_until(first_target=50.0, second_target=120.0),
        ),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value == (100.0, 120.0)


def test_get_time_returns_virtual_clock() -> None:
    @do
    def program():
        return (yield GetTime())

    result = run(
        WithHandler(sim_time_handler(start_time=42.5), program()),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value == 42.5


def test_schedule_at_fires_when_clock_passes_target() -> None:
    marker: dict[str, Any] = {
        "before_target": None,
        "after_target": None,
        "fired_at": None,
    }

    @do
    def mark_fired():
        marker["fired_at"] = yield GetTime()
        marker["after_target"] = True

    @do
    def program():
        now = yield GetTime()
        yield ScheduleAt(now + 5.0, mark_fired())
        yield Delay(4.0)
        marker["before_target"] = marker["after_target"] is True
        yield Delay(1.0)
        return marker["before_target"], marker["after_target"], marker["fired_at"]

    result = run(
        WithHandler(sim_time_handler(start_time=10.0), program()),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value == (False, True, 15.0)


def test_set_time_jumps_clock() -> None:
    @do
    def program():
        yield SetTime(42.0)
        return (yield GetTime())

    result = run(
        WithHandler(sim_time_handler(start_time=0.0), program()),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value == 42.0


def test_delegates_spawn_to_core_scheduler() -> None:
    seen_spawn = {"value": False}

    def probe_spawn(effect: Any, k: Any):
        if isinstance(effect, SpawnEffect):
            seen_spawn["value"] = True
        yield Pass()

    @do
    def worker():
        return "done"

    @do
    def program():
        return (yield Spawn(worker()))

    result = run(
        WithHandler(
            probe_spawn,
            WithHandler(sim_time_handler(start_time=0.0), program()),
        ),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert isinstance(result.value, Task)
    assert seen_spawn["value"] is True


def test_delegates_wait_to_core_scheduler() -> None:
    @do
    def worker():
        yield Delay(1.0)
        return "done"

    @do
    def program():
        task = yield Spawn(worker())
        value = yield Wait(task)
        now = yield GetTime()
        return value, now

    result = run(
        WithHandler(sim_time_handler(start_time=0.0), program()),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value == ("done", 1.0)


def test_delegates_gather_to_core_scheduler() -> None:
    seen_gather = {"value": False}

    def probe_gather(effect: Any, k: Any):
        if isinstance(effect, GatherEffect):
            seen_gather["value"] = True
        yield Pass()

    @do
    def worker(name: str):
        return name

    @do
    def program():
        first = yield Spawn(worker("a"))
        second = yield Spawn(worker("b"))
        return (yield gather(first, second))

    result = run(
        WithHandler(
            probe_gather,
            WithHandler(sim_time_handler(start_time=0.0), program()),
        ),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value == ["a", "b"]
    assert seen_gather["value"] is True


@dataclass(frozen=True)
class MarketOpen:
    time: float


def test_composes_with_event_handler() -> None:
    @do
    def consumer():
        received = yield WaitForEvent(MarketOpen)
        now = yield GetTime()
        return received, now

    @do
    def program():
        consumer_task = yield Spawn(consumer())
        now = yield GetTime()
        target = now + 60.0
        yield ScheduleAt(target, Publish(MarketOpen(time=target)))
        yield Delay(60.0)
        consumer_result = yield Wait(consumer_task)
        end = yield GetTime()
        return consumer_result, end

    result = run(
        WithHandler(
            sim_time_handler(start_time=1_704_067_200.0),
            WithHandler(event_handler(), program()),
        ),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    (event, event_seen_at), end = result.value
    assert isinstance(event, MarketOpen)
    assert event.time == 1_704_067_260.0
    assert event_seen_at == 1_704_067_260.0
    assert end == 1_704_067_260.0


@dataclass(frozen=True)
class PriceTick:
    value: int


def test_spawn_wait_for_event_publish_interleaving() -> None:
    @do
    def listener():
        event = yield WaitForEvent(PriceTick)
        now = yield GetTime()
        return event.value, now

    @do
    def publisher():
        yield Delay(5.0)
        now = yield GetTime()
        yield Publish(PriceTick(value=1))
        return now

    @do
    def program():
        listener_task = yield Spawn(listener())
        publisher_task = yield Spawn(publisher())
        listener_result = yield Wait(listener_task)
        publisher_time = yield Wait(publisher_task)
        end = yield GetTime()
        return listener_result, publisher_time, end

    result = run(
        WithHandler(
            sim_time_handler(start_time=0.0),
            WithHandler(event_handler(), program()),
        ),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value == ((1, 5.0), 5.0, 5.0)


def test_log_formatter_stamps_tell_messages() -> None:
    @do
    def program():
        from doeff import Tell

        yield Tell("first")
        yield Delay(2.5)
        yield Tell("second")
        return "ok"

    wrapped = Listen(
        WithHandler(
            sim_time_handler(
                start_time=7.5,
                log_formatter=lambda sim_time, msg: f"[sim:{sim_time:.1f}] {msg}",
            ),
            program(),
        )
    )

    result = run(wrapped, handlers=default_handlers())
    assert result.is_ok()
    listen_result = result.value
    assert listen_result.value == "ok"
    assert list(listen_result.log) == ["[sim:7.5] first", "[sim:10.0] second"]
