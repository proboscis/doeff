
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from doeff_events import Publish, WaitForEvent, event_handler
from doeff_time import Delay, GetTime, ScheduleAt, SetTime, WaitUntil, sim_time_handler

from doeff import (
    Effect,
    Gather,
    Listen,
    Pass,
    Spawn,
    Tell,
    Wait,
    WithHandler,
    default_handlers,
    do,
    run,
)
from doeff.effects.gather import GatherEffect
from doeff.effects.spawn import PRIORITY_HIGH, SpawnEffect


def _run_with_sim(
    program: Any,
    *,
    start_time: float = 0.0,
    log_formatter: Callable[[float, Any], str] | None = None,
):
    return run(
        WithHandler(
            sim_time_handler(start_time=start_time, log_formatter=log_formatter),
            program,
        ),
        handlers=default_handlers(),
    )


def _run_with_sim_and_events(
    program: Any,
    *,
    start_time: float = 0.0,
    log_formatter: Callable[[float, Any], str] | None = None,
):
    return run(
        WithHandler(
            sim_time_handler(start_time=start_time, log_formatter=log_formatter),
            WithHandler(event_handler(), program),
        ),
        handlers=default_handlers(),
    )


def _run_with_probe(
    program: Any,
    *,
    effect_type: type[Any],
):
    seen = 0

    @do
    def probe(effect: Effect, k: Any):
        nonlocal seen
        if isinstance(effect, effect_type):
            seen += 1
        yield Pass()

    result = run(
        WithHandler(
            probe,
            WithHandler(sim_time_handler(start_time=0.0), program),
        ),
        handlers=default_handlers(),
    )
    return seen, result


@do
def _read_time():
    return (yield GetTime())


def test_delay_advances_virtual_clock() -> None:
    @do
    def _program():
        before = yield GetTime()
        yield Delay(5.0)
        after = yield GetTime()
        return before, after

    result = _run_with_sim(_program(), start_time=100.0)
    assert result.value == (100.0, 105.0)


def test_wait_until_advances_virtual_clock() -> None:
    @do
    def _program():
        before = yield GetTime()
        yield WaitUntil(14.0)
        after = yield GetTime()
        return before, after

    result = _run_with_sim(_program(), start_time=10.0)
    assert result.value == (10.0, 14.0)


def test_get_time_returns_virtual_clock() -> None:
    result = _run_with_sim(_read_time(), start_time=1_704_067_200.0)
    assert result.value == 1_704_067_200.0


def test_set_time_jumps_clock() -> None:
    @do
    def _program():
        yield SetTime(42.0)
        return (yield GetTime())

    result = _run_with_sim(_program(), start_time=0.0)
    assert result.value == 42.0


def test_clock_never_advances_while_normal_tasks_runnable() -> None:
    @do
    def _immediate():
        return (yield GetTime())

    @do
    def _program():
        first_task = yield Spawn(_immediate())
        second_task = yield Spawn(_immediate())
        immediate_values = yield Gather(first_task, second_task)
        now = yield GetTime()
        return immediate_values, now

    result = _run_with_sim(_program(), start_time=0.0)
    assert result.value == ([0.0, 0.0], 0.0)


def test_concurrent_delays_resolve_in_time_order() -> None:
    @do
    def _program():
        yield Delay(1.0)
        first = yield GetTime()
        yield Delay(2.0)
        second = yield GetTime()
        return first, second

    result = _run_with_sim(_program(), start_time=0.0)
    assert result.value == (1.0, 3.0)


def test_clock_driver_only_runs_at_idle_priority() -> None:
    @do
    def _high_priority_worker():
        return (yield GetTime())

    @do
    def _program():
        worker_task = yield Spawn(_high_priority_worker(), priority=PRIORITY_HIGH)
        worker_time = yield Wait(worker_task)
        yield Delay(1.0)
        now = yield GetTime()
        return worker_time, now

    result = _run_with_sim(_program(), start_time=0.0)
    assert result.value == (0.0, 1.0)


def test_sim_time_preemption_preserves_each_task_wake_time() -> None:
    @do
    def _sleep_and_report(name: str, delay_seconds: float):
        start = yield GetTime()
        yield Delay(delay_seconds)
        woke = yield GetTime()
        yield Tell((name, start, woke))
        return woke

    @do
    def _program():
        t1 = yield Spawn(_sleep_and_report("t1", 1.0))
        t2 = yield Spawn(_sleep_and_report("t2", 3.0))
        t3 = yield Spawn(_sleep_and_report("t3", 2.0))
        wake_times = yield Gather(t1, t2, t3)
        final_time = yield GetTime()
        return wake_times, final_time

    result = _run_with_sim(Listen(_program()), start_time=10.0)
    listen_result = result.value
    wake_times, final_time = listen_result.value

    assert wake_times == [11.0, 13.0, 12.0]
    assert final_time == 13.0
    assert list(listen_result.log) == [
        ("t1", 10.0, 11.0),
        ("t3", 10.0, 12.0),
        ("t2", 10.0, 13.0),
    ]


def test_multiple_tasks_delay_simultaneously() -> None:
    @do
    def _program():
        start = yield GetTime()
        target = start + 1.0
        yield WaitUntil(target)
        first = yield GetTime()
        yield WaitUntil(target)
        second = yield GetTime()
        now = yield GetTime()
        return first, second, now

    result = _run_with_sim(_program(), start_time=0.0)
    assert result.value == (1.0, 1.0, 1.0)


def test_delegates_spawn_to_core_scheduler() -> None:
    @do
    def _child():
        return "child-ok"

    @do
    def _program():
        task = yield Spawn(_child())
        return (yield Wait(task))

    seen, result = _run_with_probe(_program(), effect_type=SpawnEffect)
    assert result.value == "child-ok"
    assert seen >= 1


def test_delegates_wait_to_core_scheduler() -> None:
    @do
    def _child():
        return "wait-ok"

    @do
    def _program():
        task = yield Spawn(_child())
        return (yield Wait(task))

    seen, result = _run_with_probe(_program(), effect_type=GatherEffect)
    assert result.value == "wait-ok"
    assert seen >= 1


def test_delegates_gather_to_core_scheduler() -> None:
    @do
    def _worker(value: int):
        return value

    @do
    def _program():
        t1 = yield Spawn(_worker(1))
        t2 = yield Spawn(_worker(2))
        return (yield Gather(t1, t2))

    seen, result = _run_with_probe(_program(), effect_type=GatherEffect)
    assert result.value == [1, 2]
    assert seen >= 1


def test_spawn_runs_immediately_not_lazy() -> None:
    @do
    def _listener():
        return (yield WaitForEvent(str))

    @do
    def _program():
        listener_task = yield Spawn(_listener())
        yield Delay(0.0)
        yield Publish("ready")
        return (yield Wait(listener_task))

    result = _run_with_sim_and_events(_program(), start_time=0.0)
    assert result.value == "ready"


def test_schedule_at_fires_when_clock_passes_target() -> None:
    @do
    def _program():
        now = yield GetTime()
        yield ScheduleAt(now + 5.0, Tell("scheduled"))
        yield Delay(5.0)
        return (yield GetTime())

    result = _run_with_sim(Listen(_program()), start_time=10.0)
    listen_result = result.value
    assert listen_result.value == 15.0
    assert list(listen_result.log) == ["scheduled"]


def test_schedule_at_past_time_fires_immediately() -> None:
    @do
    def _program():
        yield SetTime(10.0)
        yield ScheduleAt(5.0, Tell("past"))
        yield Delay(0.0)
        return (yield GetTime())

    result = _run_with_sim(Listen(_program()), start_time=0.0)
    listen_result = result.value
    assert listen_result.value == 10.0
    assert list(listen_result.log) == ["past"]


@dataclass(frozen=True)
class MarketOpen:
    time: float


def test_composes_with_event_handler() -> None:
    @do
    def _listener():
        return (yield WaitForEvent(MarketOpen))

    @do
    def _program():
        listener_task = yield Spawn(_listener())
        yield Delay(0.0)
        now = yield GetTime()
        expected_event = MarketOpen(time=now + 1.0)
        yield Publish(expected_event)
        return (yield Wait(listener_task))

    result = _run_with_sim_and_events(_program(), start_time=1_704_067_200.0)
    event = result.value
    assert isinstance(event, MarketOpen)
    assert event.time == 1_704_067_201.0


def test_spawn_wait_for_event_publish_interleaving() -> None:
    @do
    def _listener():
        return (yield WaitForEvent(str))

    @do
    def _program():
        listener_task = yield Spawn(_listener())
        yield Delay(0.0)
        yield Publish("tick")
        return (yield Wait(listener_task))

    result = _run_with_sim_and_events(_program(), start_time=0.0)
    assert result.value == "tick"


def test_log_formatter_stamps_tell_messages() -> None:
    @do
    def _program():
        yield Tell("hello")
        yield Delay(2.0)
        yield Tell("world")
        return "ok"

    result = run(
        Listen(
            WithHandler(
                sim_time_handler(
                    start_time=7.5,
                    log_formatter=lambda sim_time, msg: f"[sim:{sim_time:.1f}] {msg}",
                ),
                _program(),
            )
        ),
        handlers=default_handlers(),
    )
    listen_result = result.value

    assert listen_result.value == "ok"
    assert list(listen_result.log) == ["[sim:7.5] hello", "[sim:9.5] world"]
