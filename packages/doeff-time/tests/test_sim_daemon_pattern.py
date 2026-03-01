
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from conftest import SIM_TIME_EPOCH, sim_time
from doeff_events import Publish, WaitForEvent, event_handler
from doeff_time import Delay, GetTime, ScheduleAt, WaitUntil, sim_time_handler

from doeff import Gather, Spawn, Wait, WithHandler, default_handlers, do, run


def _run_sim(
    program: Any,
    *,
    start_time: datetime = SIM_TIME_EPOCH,
):
    return run(
        WithHandler(sim_time_handler(start_time=start_time), program),
        handlers=default_handlers(),
    )


def _run_sim_events(
    program: Any,
    *,
    start_time: datetime = SIM_TIME_EPOCH,
):
    return run(
        WithHandler(
            sim_time_handler(start_time=start_time),
            WithHandler(event_handler(), program),
        ),
        handlers=default_handlers(),
    )


def _run_sim_events_with_watchdog(
    program_factory: Callable[[], Any],
    *,
    timeout_seconds: float = 2.0,
    start_time: datetime = SIM_TIME_EPOCH,
):
    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _worker() -> None:
        try:
            result["value"] = _run_sim_events(program_factory(), start_time=start_time)
        except BaseException as exc:
            error["value"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if thread.is_alive():
        pytest.fail(
            "Program did not complete within watchdog budget; "
            "scheduler may be waiting for non-terminating spawned daemons."
        )
    if "value" in error:
        raise error["value"]
    return result["value"]


@dataclass(frozen=True)
class NewsEvent:
    score: float


@dataclass(frozen=True)
class MarketSignal:
    score: float


@dataclass(frozen=True)
class FeedDone:
    pass


@dataclass(frozen=True)
class ReadyEvent:
    value: str


@dataclass(frozen=True)
class ScheduledEvent:
    name: str


def test_spawned_service_publishes_events_received_by_main() -> None:
    historical_events = [
        (sim_time(1.0), 0.1),
        (sim_time(2.0), -0.2),
        (sim_time(3.0), 0.3),
    ]

    @do
    def news_fetcher_daemon():
        for scheduled_time, score in historical_events:
            yield WaitUntil(scheduled_time)
            yield Publish(NewsEvent(score=score))

    @do
    def main():
        yield Spawn(news_fetcher_daemon())
        received = []
        for _ in historical_events:
            event = yield WaitForEvent(NewsEvent)
            now = yield GetTime()
            received.append((event.score, now))
        return received

    result = _run_sim_events(main(), start_time=sim_time(0.0))
    assert result.value == [
        (0.1, sim_time(1.0)),
        (-0.2, sim_time(2.0)),
        (0.3, sim_time(3.0)),
    ]


def test_virtual_time_advances_across_multiple_spawned_tasks() -> None:
    @do
    def worker(name: str, wake_at: datetime):
        yield WaitUntil(wake_at)
        return name, (yield GetTime())

    @do
    def program():
        early = yield Spawn(worker("early", sim_time(5.0)))
        late = yield Spawn(worker("late", sim_time(10.0)))
        results = yield Gather(early, late)
        return results, (yield GetTime())

    result = _run_sim(program(), start_time=sim_time(0.0))
    assert result.value == (
        [("early", sim_time(5.0)), ("late", sim_time(10.0))],
        sim_time(10.0),
    )


def test_news_signal_trade_pipeline_with_spawn_and_events() -> None:
    historical_events = [
        (sim_time(1.0), 0.1),
        (sim_time(2.0), -0.2),
        (sim_time(3.0), 0.3),
    ]

    @do
    def news_fetcher_daemon():
        for scheduled_time, score in historical_events:
            yield WaitUntil(scheduled_time)
            yield Publish(NewsEvent(score=score))
        # Yield one scheduler turn so listeners can re-register before stop.
        yield Delay(0.0)
        yield Publish(FeedDone())

    @do
    def signal_generator_daemon():
        while True:
            event = yield WaitForEvent(NewsEvent, FeedDone)
            if isinstance(event, FeedDone):
                break
            yield Publish(MarketSignal(score=event.score * 10.0))

    @do
    def execute_trade(signal: MarketSignal):
        now = yield GetTime()
        return signal.score, now

    @do
    def trading_strategy():
        results = []
        for _ in historical_events:
            signal = yield WaitForEvent(MarketSignal)
            results.append((yield execute_trade(signal)))
        return results

    @do
    def backtest():
        news_task = yield Spawn(news_fetcher_daemon())
        signal_task = yield Spawn(signal_generator_daemon())
        trades = yield trading_strategy()
        yield Wait(news_task)
        yield Wait(signal_task)
        return trades

    result = _run_sim_events(backtest(), start_time=sim_time(0.0))
    assert result.value == [
        (1.0, sim_time(1.0)),
        (-2.0, sim_time(2.0)),
        (3.0, sim_time(3.0)),
    ]


def test_main_program_returns_while_daemons_still_running() -> None:
    @do
    def daemon_waiting_forever():
        while True:
            yield WaitForEvent(ScheduledEvent)

    @do
    def one_shot_publisher():
        yield Delay(1.0)
        yield Publish(ReadyEvent(value="done"))

    @do
    def main():
        yield Spawn(daemon_waiting_forever())
        publisher = yield Spawn(one_shot_publisher())
        ready = yield WaitForEvent(ReadyEvent)
        yield Wait(publisher)
        return ready

    result = _run_sim_events_with_watchdog(main, start_time=sim_time(0.0))
    assert result.value == ReadyEvent(value="done")


def test_schedule_at_composes_with_event_handler() -> None:
    @do
    def wait_and_return():
        event = yield WaitForEvent(ScheduledEvent)
        now = yield GetTime()
        return event, now

    @do
    def program():
        listener = yield Spawn(wait_and_return())
        now = yield GetTime()
        yield ScheduleAt(now + timedelta(seconds=60.0), Publish(ScheduledEvent(name="market_open")))
        yield Delay(60.0)
        return (yield Wait(listener))

    result = _run_sim_events(program(), start_time=sim_time(1_000.0))
    event, observed_time = result.value
    assert event == ScheduledEvent(name="market_open")
    assert observed_time == sim_time(1_060.0)
