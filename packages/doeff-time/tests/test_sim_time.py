from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff import (
    Delegate,
    Gather,
    GatherEffect,
    Listen,
    PRIORITY_NORMAL,
    ProgramBase,
    Resume,
    Spawn,
    SpawnEffect,
    Tell,
    Wait,
    WithHandler,
    default_handlers,
    do,
    run,
)
from doeff_events import Publish, WaitForEvent, event_handler
from doeff_time.effects import Delay, GetTime, ScheduleAt, WaitUntil


def _sim_time_handler(*, start_time: float, log_formatter: Any | None = None):
    from doeff_time.handlers import sim_time_handler

    return sim_time_handler(start_time=start_time, log_formatter=log_formatter)


def _set_time(time: float):
    from doeff_time.effects import SetTime

    return SetTime(time)


def _run_sim(program: Any, *, start_time: float, log_formatter: Any | None = None):
    result = run(
        WithHandler(_sim_time_handler(start_time=start_time, log_formatter=log_formatter), program),
        handlers=default_handlers(),
    )
    assert result.is_ok()
    return result.value


def _run_sim_with_events(program: Any, *, start_time: float):
    wrapped = WithHandler(
        _sim_time_handler(start_time=start_time),
        WithHandler(event_handler(), program),
    )
    result = run(wrapped, handlers=default_handlers())
    assert result.is_ok()
    return result.value


def test_delay_advances_virtual_clock() -> None:
    @do
    def program():
        before = yield GetTime()
        yield Delay(2.5)
        after = yield GetTime()
        return before, after

    assert _run_sim(program(), start_time=10.0) == (10.0, 12.5)


def test_wait_until_advances_virtual_clock() -> None:
    @do
    def program():
        before = yield GetTime()
        yield WaitUntil(15.0)
        after = yield GetTime()
        return before, after

    assert _run_sim(program(), start_time=12.0) == (12.0, 15.0)


def test_get_time_returns_virtual_clock() -> None:
    @do
    def program():
        return (yield GetTime())

    assert _run_sim(program(), start_time=123.456) == 123.456


def test_set_time_jumps_clock() -> None:
    @do
    def program():
        yield _set_time(42.0)
        jumped = yield GetTime()
        yield Delay(1.5)
        after = yield GetTime()
        return jumped, after

    assert _run_sim(program(), start_time=0.0) == (42.0, 43.5)


def test_clock_never_advances_while_normal_tasks_runnable() -> None:
    observed: dict[str, float] = {}

    @do
    def delayed_task():
        yield Delay(5.0)
        observed["delayed"] = yield GetTime()
        return None

    @do
    def runnable_task():
        observed["runnable"] = yield GetTime()
        return None

    @do
    def program():
        delayed = yield Spawn(delayed_task())
        runnable = yield Spawn(runnable_task())
        _ = yield Gather(delayed, runnable)
        return observed["runnable"], observed["delayed"]

    assert _run_sim(program(), start_time=0.0) == (0.0, 5.0)


def test_concurrent_delays_resolve_in_time_order() -> None:
    resumed: list[tuple[str, float]] = []

    @do
    def worker(name: str, seconds: float):
        yield Delay(seconds)
        resumed.append((name, (yield GetTime())))
        return name

    @do
    def program():
        long_task = yield Spawn(worker("long", 2.0))
        short_task = yield Spawn(worker("short", 1.0))
        _ = yield Gather(long_task, short_task)
        return tuple(resumed)

    resolved = _run_sim(program(), start_time=0.0)
    assert [name for name, _ in resolved] == ["short", "long"]
    assert resolved[0][1] <= resolved[1][1]
    assert resolved[1][1] == 2.0


def test_clock_driver_only_runs_at_idle_priority() -> None:
    events: list[tuple[str, float]] = []

    @do
    def delayed():
        events.append(("delay-start", (yield GetTime())))
        yield Delay(1.0)
        events.append(("delay-end", (yield GetTime())))
        return None

    @do
    def normal():
        events.append(("normal", (yield GetTime())))
        return None

    @do
    def program():
        delayed_task = yield Spawn(delayed(), priority=PRIORITY_NORMAL)
        normal_task = yield Spawn(normal(), priority=PRIORITY_NORMAL)
        _ = yield Gather(delayed_task, normal_task)
        return tuple(events)

    assert _run_sim(program(), start_time=0.0) == (
        ("delay-start", 0.0),
        ("normal", 0.0),
        ("delay-end", 1.0),
    )


def test_multiple_tasks_delay_simultaneously() -> None:
    @do
    def worker(label: str):
        yield Delay(1.0)
        return label, (yield GetTime())

    @do
    def program():
        first = yield Spawn(worker("a"))
        second = yield Spawn(worker("b"))
        third = yield Spawn(worker("c"))
        return (yield Gather(first, second, third))

    values = _run_sim(program(), start_time=0.0)
    assert [name for name, _ in values] == ["a", "b", "c"]
    assert {current_time for _, current_time in values} == {1.0}


def test_delegates_spawn_to_core_scheduler() -> None:
    observed = {"spawn": 0}

    def probe(effect: Any, k: Any):
        if isinstance(effect, SpawnEffect):
            observed["spawn"] += 1
        delegated = yield Delegate()
        return (yield Resume(k, delegated))

    @do
    def child():
        return "spawned"

    @do
    def program():
        spawned = yield Spawn(child())
        return (yield Wait(spawned))

    wrapped = WithHandler(
        probe,
        WithHandler(_sim_time_handler(start_time=0.0), program()),
    )
    result = run(wrapped, handlers=default_handlers())
    assert result.is_ok()
    assert result.value == "spawned"
    assert observed["spawn"] == 1


def test_delegates_wait_to_core_scheduler() -> None:
    def probe(effect: Any, k: Any):
        delegated = yield Delegate()
        return (yield Resume(k, delegated))

    @do
    def child():
        return "ready"

    @do
    def program():
        task = yield Spawn(child())
        return (yield Wait(task))

    wrapped = WithHandler(
        probe,
        WithHandler(_sim_time_handler(start_time=0.0), program()),
    )
    result = run(wrapped, handlers=default_handlers())
    assert result.is_ok()
    assert result.value == "ready"


def test_delegates_gather_to_core_scheduler() -> None:
    observed = {"gather": 0}

    def probe(effect: Any, k: Any):
        if isinstance(effect, GatherEffect):
            observed["gather"] += 1
        delegated = yield Delegate()
        return (yield Resume(k, delegated))

    @do
    def worker(label: str):
        return label

    @do
    def program():
        left = yield Spawn(worker("left"))
        right = yield Spawn(worker("right"))
        return (yield Gather(left, right))

    wrapped = WithHandler(
        probe,
        WithHandler(_sim_time_handler(start_time=0.0), program()),
    )
    result = run(wrapped, handlers=default_handlers())
    assert result.is_ok()
    assert result.value == ["left", "right"]
    assert observed["gather"] == 1


def test_spawn_runs_immediately_not_lazy() -> None:
    events: list[str] = []

    @do
    def child():
        events.append("child-start")
        return "child-done"

    @do
    def program():
        task = yield Spawn(child())
        events.append("after-spawn")
        yield Delay(0.0)
        events.append("after-delay")
        value = yield Wait(task)
        events.append("after-wait")
        return value, tuple(events)

    value, timeline = _run_sim(program(), start_time=0.0)
    assert value == "child-done"
    assert timeline.index("child-start") < timeline.index("after-delay")
    assert timeline[-1] == "after-wait"


def test_schedule_at_fires_when_clock_passes_target() -> None:
    fired_times: list[float] = []

    @do
    def scheduled():
        fired_times.append((yield GetTime()))
        return None

    @do
    def program():
        now = yield GetTime()
        yield ScheduleAt(now + 5.0, scheduled())
        yield Delay(5.0)
        yield Delay(0.0)
        return tuple(fired_times), (yield GetTime())

    fired, now = _run_sim(program(), start_time=0.0)
    assert fired == (5.0,)
    assert now == 5.0


def test_schedule_at_past_time_fires_immediately() -> None:
    fired_times: list[float] = []

    @do
    def scheduled():
        fired_times.append((yield GetTime()))
        return None

    @do
    def program():
        yield ScheduleAt(5.0, scheduled())
        yield Delay(0.0001)
        return tuple(fired_times), (yield GetTime())

    fired, now = _run_sim(program(), start_time=10.0)
    assert len(fired) == 1
    assert fired[0] == now
    assert now > 10.0


@dataclass(frozen=True)
class MarketOpen:
    at: float


def test_composes_with_event_handler() -> None:
    @do
    def waiter():
        event = yield WaitForEvent(MarketOpen)
        now = yield GetTime()
        return event, now

    @do
    def publisher():
        now = yield GetTime()
        target = now + 2.0
        yield ScheduleAt(target, Publish(MarketOpen(at=target)))
        yield Delay(2.0)
        return "published"

    @do
    def program():
        waiter_task = yield Spawn(waiter())
        publisher_task = yield Spawn(publisher())
        waiter_result = yield Wait(waiter_task)
        publish_result = yield Wait(publisher_task)
        return waiter_result, publish_result

    waiter_result, publish_result = _run_sim_with_events(
        program(),
        start_time=1_704_067_200.0,
    )
    event, observed_time = waiter_result
    assert publish_result == "published"
    assert event == MarketOpen(at=1_704_067_202.0)
    assert observed_time == 1_704_067_202.0


@dataclass(frozen=True)
class OrderFilled:
    symbol: str
    quantity: int


def test_spawn_wait_for_event_publish_interleaving() -> None:
    published = OrderFilled(symbol="AAPL", quantity=100)

    @do
    def listener():
        return (yield WaitForEvent(OrderFilled))

    @do
    def publisher():
        yield Publish(published)
        return "sent"

    @do
    def program():
        listener_task = yield Spawn(listener())
        publisher_task = yield Spawn(publisher())
        received = yield Wait(listener_task)
        status = yield Wait(publisher_task)
        now = yield GetTime()
        return received, status, now

    received, status, now = _run_sim_with_events(program(), start_time=0.0)
    assert received == published
    assert status in {None, "sent"}
    assert now == 0.0


def test_log_formatter_stamps_tell_messages() -> None:
    @do
    def program():
        yield Tell("alpha")
        yield Delay(2.0)
        yield Tell("omega")
        return "ok"

    wrapped = Listen(
        WithHandler(
            _sim_time_handler(
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
    assert list(listen_result.log) == ["[sim:7.5] alpha", "[sim:9.5] omega"]
