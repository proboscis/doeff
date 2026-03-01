"""Migration-readiness tests for proboscis-ema backtest patterns.

These tests exercise the real-world concurrency and time patterns used in
proboscis-ema's DeterministicSimulationInterpreter, verifying that
sim_time_handler + core scheduler + event_handler can replace it.

Pattern mapping (proboscis-ema → doeff):
    sim_submit(prog)          → Spawn(prog)
    sim_await(task)           → Wait(task)
    sim_await_all(t1, t2)    → Gather(t1, t2)
    sim_await_first(t1, t2)  → Race(t1, t2)
    sim_delay(seconds)        → Delay(seconds)
    sim_wait_until(target)    → WaitUntil(target)
    sim_get_time()            → GetTime()
    sim_set_time(ts)          → SetTime(ts)
    sim_publish(event)        → Publish(event)
    sim_wait_for_event(T)     → WaitForEvent(T)
    sim_invoke_at(prog, t)    → ScheduleAt(t, prog)
"""


from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from doeff_events import Publish, WaitForEvent, event_handler
from doeff_time import Delay, GetTime, ScheduleAt, SetTime, WaitUntil, sim_time_handler

from doeff import (
    Gather,
    Listen,
    Race,
    Spawn,
    Tell,
    Wait,
    WithHandler,
    default_handlers,
    do,
    run,
)

SIM_TIME_EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)


def sim_time(seconds: float) -> datetime:
    return SIM_TIME_EPOCH + timedelta(seconds=seconds)


def sim_seconds(value: datetime) -> float:
    return (value - SIM_TIME_EPOCH).total_seconds()


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _run_sim(
    program: Any,
    *,
    start_time: datetime = SIM_TIME_EPOCH,
    log_formatter: Callable[[datetime, Any], str] | None = None,
):
    return run(
        WithHandler(
            sim_time_handler(start_time=start_time, log_formatter=log_formatter),
            program,
        ),
        handlers=default_handlers(),
    )


def _run_sim_events(
    program: Any,
    *,
    start_time: datetime = SIM_TIME_EPOCH,
    log_formatter: Callable[[datetime, Any], str] | None = None,
):
    return run(
        WithHandler(
            sim_time_handler(start_time=start_time, log_formatter=log_formatter),
            WithHandler(event_handler(), program),
        ),
        handlers=default_handlers(),
    )


# ---------------------------------------------------------------------------
# Event types (mimicking proboscis-ema market events)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MarketSignal:
    symbol: str
    price: float


@dataclass(frozen=True)
class TradeCloseRequest:
    trade_id: str


@dataclass(frozen=True)
class BacktestStop:
    pass


@dataclass(frozen=True)
class OrderFill:
    trade_id: str
    fill_price: float


# ---------------------------------------------------------------------------
# Pattern 1: Two tasks with different delays — shorter finishes first
#
# proboscis-ema: sim_submit(worker("fast", 1.0)), sim_submit(worker("slow", 3.0))
# The time queue should interleave correctly: fast@1.0, slow@3.0
# ---------------------------------------------------------------------------


class TestConcurrentTaskDelayInterleaving:
    def test_two_tasks_shorter_delay_finishes_first(self) -> None:
        @do
        def _worker(label: str, delay_seconds: float):
            yield Delay(delay_seconds)
            now = yield GetTime()
            return f"{label}@{sim_seconds(now):.1f}"

        @do
        def _program():
            fast = yield Spawn(_worker("fast", 1.0))
            slow = yield Spawn(_worker("slow", 3.0))
            fast_result = yield Wait(fast)
            slow_result = yield Wait(slow)
            return fast_result, slow_result

        result = _run_sim(_program(), start_time=sim_time(0.0))
        assert result.value == ("fast@1.0", "slow@3.0")

    def test_three_tasks_ordered_by_delay_magnitude(self) -> None:
        @do
        def _worker(label: str, delay_seconds: float):
            yield Delay(delay_seconds)
            now = yield GetTime()
            return f"{label}@{sim_seconds(now):.1f}"

        @do
        def _program():
            t1 = yield Spawn(_worker("A", 5.0))
            t2 = yield Spawn(_worker("B", 2.0))
            t3 = yield Spawn(_worker("C", 8.0))
            results = yield Gather(t1, t2, t3)
            return results

        result = _run_sim(_program(), start_time=sim_time(0.0))
        assert result.value == ["A@5.0", "B@2.0", "C@8.0"]

    def test_tasks_at_same_delay_both_see_same_time(self) -> None:
        @do
        def _worker(label: str):
            yield Delay(5.0)
            now = yield GetTime()
            return f"{label}@{sim_seconds(now):.1f}"

        @do
        def _program():
            t1 = yield Spawn(_worker("X"))
            t2 = yield Spawn(_worker("Y"))
            return (yield Gather(t1, t2))

        result = _run_sim(_program(), start_time=sim_time(10.0))
        assert result.value == ["X@15.0", "Y@15.0"]


# ---------------------------------------------------------------------------
# Pattern 2: Multiple concurrent services (the 5-service backtest pattern)
#
# proboscis-ema:
#   yield sim_submit(price_provider.run(), daemon=True)
#   yield sim_submit(signal_generator.run(), daemon=True)
#   yield sim_submit(strategy.run(), daemon=True)
#   ...main loop awaits stop event...
#
# We model this as multiple spawned "service" tasks that each run a
# while-loop processing events, plus a coordinator that stops them.
# ---------------------------------------------------------------------------


class TestMultiServiceBacktestPattern:
    def test_producer_consumer_service_pair(self) -> None:
        """Price feed produces signals, strategy consumes them."""

        @do
        def _price_feed():
            """Produces MarketSignal events at regular intervals."""
            prices = [100.0, 101.5, 99.0]
            for price in prices:
                yield Delay(1.0)
                yield Publish(MarketSignal(symbol="BTC", price=price))
            # Ensure strategy has a chance to re-register its next WaitForEvent.
            yield Delay(0.0)
            yield Publish(BacktestStop())

        @do
        def _strategy():
            """Consumes MarketSignal events until BacktestStop."""
            signals: list[MarketSignal] = []
            while True:
                event = yield WaitForEvent(MarketSignal, BacktestStop)
                if isinstance(event, BacktestStop):
                    break
                signals.append(event)
            return signals

        @do
        def _program():
            strategy_task = yield Spawn(_strategy())
            yield Spawn(_price_feed())
            return (yield Wait(strategy_task))

        result = _run_sim_events(_program(), start_time=sim_time(0.0))
        signals = result.value
        assert len(signals) == 3
        assert [s.price for s in signals] == [100.0, 101.5, 99.0]

    def test_three_services_coordinate_via_events(self) -> None:
        """Three services run concurrently: feed → processor → collector."""
        @do
        def _feed_service():
            for i in range(3):
                yield Delay(1.0)
                yield Publish(MarketSignal(symbol="ETH", price=float(i)))
            yield Delay(1.0)
            yield Publish(BacktestStop())

        @do
        def _processor_service():
            """Reads signals, publishes fills."""
            while True:
                event = yield WaitForEvent(MarketSignal, BacktestStop)
                if isinstance(event, BacktestStop):
                    yield Publish(BacktestStop())
                    break
                yield Publish(OrderFill(trade_id=f"T{int(event.price)}", fill_price=event.price * 2))

        @do
        def _collector_service():
            """Collects fills until stop."""
            fills: list[OrderFill] = []
            while True:
                event = yield WaitForEvent(OrderFill, BacktestStop)
                if isinstance(event, BacktestStop):
                    break
                fills.append(event)
            return fills

        @do
        def _program():
            collector = yield Spawn(_collector_service())
            yield Spawn(_processor_service())
            yield Spawn(_feed_service())
            return (yield Wait(collector))

        result = _run_sim_events(_program(), start_time=sim_time(0.0))
        fills = result.value
        assert len(fills) == 3
        assert [f.trade_id for f in fills] == ["T0", "T1", "T2"]
        assert [f.fill_price for f in fills] == [0.0, 2.0, 4.0]


# ---------------------------------------------------------------------------
# Pattern 3: Race between time expiry and event (holding-period pattern)
#
# proboscis-ema:
#   winner = yield await_first(
#       sim_wait_until(close_time),
#       sim_wait_for_event(TradeCloseRequestEvent),
#       auto_cancel=True,
#   )
#
# In doeff this becomes: Race two spawned tasks — one WaitUntil, one WaitForEvent.
# ---------------------------------------------------------------------------


class TestRaceTimeVsEvent:
    def test_time_expiry_wins_when_no_event(self) -> None:
        """Holding period expires before any close request arrives."""

        @do
        def _wait_for_time():
            yield WaitUntil(sim_time(10.0))
            return "time_expired"

        @do
        def _wait_for_close():
            yield WaitForEvent(TradeCloseRequest)
            return "close_requested"

        @do
        def _program():
            time_task = yield Spawn(_wait_for_time())
            close_task = yield Spawn(_wait_for_close())
            winner = yield Race(time_task, close_task)
            now = yield GetTime()
            return winner, now

        result = _run_sim_events(_program(), start_time=sim_time(0.0))
        race_result, final_time = result.value
        assert race_result.value == "time_expired"
        assert final_time == sim_time(10.0)

    def test_event_wins_when_arrives_before_expiry(self) -> None:
        """Close request arrives before holding period expires."""

        @do
        def _wait_for_time():
            yield WaitUntil(sim_time(100.0))
            return "time_expired"

        @do
        def _wait_for_close():
            yield WaitForEvent(TradeCloseRequest)
            return "close_requested"

        @do
        def _close_publisher():
            yield Delay(5.0)
            yield Publish(TradeCloseRequest(trade_id="T1"))

        @do
        def _program():
            time_task = yield Spawn(_wait_for_time())
            close_task = yield Spawn(_wait_for_close())
            yield Spawn(_close_publisher())
            winner = yield Race(close_task, time_task)
            now = yield GetTime()
            return winner, now

        result = _run_sim_events(_program(), start_time=sim_time(0.0))
        race_result, final_time = result.value
        assert race_result.value == "close_requested"
        assert final_time == sim_time(5.0)


# ---------------------------------------------------------------------------
# Pattern 4: Event-driven strategy loop with spawned workers
#
# proboscis-ema:
#   while True:
#       signal = yield sim_wait_for_event(MarketSignal, StopEvent)
#       if isinstance(signal, StopEvent): break
#       yield sim_submit(execute_trade(signal))  # fire-and-forget
#
# Verifies that spawned workers run concurrently during the event loop.
# ---------------------------------------------------------------------------


class TestEventDrivenStrategyLoop:
    def test_strategy_loop_spawns_workers_per_signal(self) -> None:
        trade_log: list[str] = []

        @do
        def _execute_trade(signal: MarketSignal):
            yield Delay(0.5)
            trade_log.append(f"filled:{signal.symbol}@{signal.price}")
            return signal.price

        @do
        def _strategy():
            tasks = []
            while True:
                event = yield WaitForEvent(MarketSignal, BacktestStop)
                if isinstance(event, BacktestStop):
                    break
                task = yield Spawn(_execute_trade(event))
                tasks.append(task)
            results = yield Gather(*tasks)
            return results

        @do
        def _market_feed():
            for price in [100.0, 200.0, 300.0]:
                yield Delay(1.0)
                yield Publish(MarketSignal(symbol="BTC", price=price))
            yield Delay(1.0)
            yield Publish(BacktestStop())

        @do
        def _program():
            strategy_task = yield Spawn(_strategy())
            yield Spawn(_market_feed())
            return (yield Wait(strategy_task))

        result = _run_sim_events(_program(), start_time=sim_time(0.0))
        assert result.value == [100.0, 200.0, 300.0]
        assert len(trade_log) == 3

    def test_strategy_accumulates_state_across_events(self) -> None:
        """Strategy maintains running state (like active position tracking)."""

        @do
        def _strategy():
            position = 0.0
            trades = 0
            while True:
                event = yield WaitForEvent(MarketSignal, BacktestStop)
                if isinstance(event, BacktestStop):
                    break
                position += event.price
                trades += 1
            return {"position": position, "trades": trades}

        @do
        def _feed():
            for price in [10.0, 20.0, 30.0]:
                yield Delay(1.0)
                yield Publish(MarketSignal(symbol="ETH", price=price))
            # Publish stop on the next scheduler turn so strategy can wait again.
            yield Delay(0.0)
            yield Publish(BacktestStop())

        @do
        def _program():
            strategy = yield Spawn(_strategy())
            yield Spawn(_feed())
            return (yield Wait(strategy))

        result = _run_sim_events(_program(), start_time=sim_time(0.0))
        assert result.value == {"position": 60.0, "trades": 3}


# ---------------------------------------------------------------------------
# Pattern 5: ScheduleAt composing with event system (sim_invoke_at)
#
# proboscis-ema: sim_invoke_at(publish(MarketOpen), at_time=open_time)
# Schedules a Publish to fire when virtual clock reaches target time.
# ---------------------------------------------------------------------------


class TestScheduleAtWithEvents:
    def test_schedule_publish_at_future_time(self) -> None:
        """ScheduleAt schedules a Publish that fires when clock advances."""

        @do
        def _listener():
            return (yield WaitForEvent(MarketSignal))

        @do
        def _program():
            listener = yield Spawn(_listener())
            yield ScheduleAt(sim_time(5.0), Publish(MarketSignal(symbol="BTC", price=50000.0)))
            yield Delay(5.0)
            return (yield Wait(listener))

        result = _run_sim_events(_program(), start_time=sim_time(0.0))
        assert isinstance(result.value, MarketSignal)
        assert result.value.price == 50000.0

    def test_multiple_scheduled_events_fire_in_order(self) -> None:
        """Multiple ScheduleAt targets fire in chronological order."""
        received: list[str] = []

        @do
        def _collector():
            for _ in range(3):
                signal = yield WaitForEvent(MarketSignal)
                received.append(f"{signal.symbol}@{signal.price}")
            return received

        @do
        def _program():
            collector = yield Spawn(_collector())
            yield ScheduleAt(sim_time(3.0), Publish(MarketSignal(symbol="C", price=3.0)))
            yield ScheduleAt(sim_time(1.0), Publish(MarketSignal(symbol="A", price=1.0)))
            yield ScheduleAt(sim_time(2.0), Publish(MarketSignal(symbol="B", price=2.0)))
            # Drive clock in steps so collector can re-register between publications.
            yield Delay(1.0)
            yield Delay(1.0)
            yield Delay(1.0)
            return (yield Wait(collector))

        result = _run_sim_events(_program(), start_time=sim_time(0.0))
        assert result.value == ["A@1.0", "B@2.0", "C@3.0"]


# ---------------------------------------------------------------------------
# Pattern 6: Task failure propagation
#
# proboscis-ema: fail_on_task_error=True means unawaited failures raise
# ---------------------------------------------------------------------------


class TestTaskFailurePropagation:
    def test_failed_task_raises_on_wait(self) -> None:
        """Wait on a failed task re-raises the exception."""

        @do
        def _failing_worker():
            yield Delay(1.0)
            raise ValueError("simulated failure")

        @do
        def _program():
            task = yield Spawn(_failing_worker())
            return (yield Wait(task))

        result = _run_sim(_program(), start_time=sim_time(0.0))
        assert result.error is not None
        assert "simulated failure" in str(result.error)

    def test_gather_propagates_first_failure(self) -> None:
        """Gather with a failing task propagates the error."""

        @do
        def _good_worker():
            yield Delay(2.0)
            return "ok"

        @do
        def _bad_worker():
            yield Delay(1.0)
            raise RuntimeError("boom")

        @do
        def _program():
            t1 = yield Spawn(_good_worker())
            t2 = yield Spawn(_bad_worker())
            return (yield Gather(t1, t2))

        result = _run_sim(_program(), start_time=sim_time(0.0))
        assert result.error is not None


# ---------------------------------------------------------------------------
# Pattern 7: WaitUntil past time returns immediately
#
# proboscis-ema: sim_wait_until(target) when target <= _current_time → noop
# ---------------------------------------------------------------------------


class TestWaitUntilPastTime:
    def test_wait_until_past_does_not_advance_clock(self) -> None:
        @do
        def _program():
            yield SetTime(sim_time(100.0))
            yield WaitUntil(sim_time(50.0))
            return (yield GetTime())

        result = _run_sim(_program(), start_time=sim_time(0.0))
        assert result.value == sim_time(100.0)

    def test_wait_until_current_time_is_noop(self) -> None:
        @do
        def _program():
            now = yield GetTime()
            yield WaitUntil(now)
            return (yield GetTime())

        result = _run_sim(_program(), start_time=sim_time(42.0))
        assert result.value == sim_time(42.0)


# ---------------------------------------------------------------------------
# Pattern 8: Gather multiple delayed tasks (sim_await_all equivalent)
#
# proboscis-ema: sim_await_all(task_a, task_b, task_c) — waits for all
# ---------------------------------------------------------------------------


class TestGatherDelayedTasks:
    def test_gather_waits_for_slowest_task(self) -> None:
        @do
        def _worker(delay: float, value: str):
            yield Delay(delay)
            return value

        @do
        def _program():
            t1 = yield Spawn(_worker(1.0, "fast"))
            t2 = yield Spawn(_worker(5.0, "slow"))
            t3 = yield Spawn(_worker(3.0, "medium"))
            results = yield Gather(t1, t2, t3)
            now = yield GetTime()
            return results, now

        result = _run_sim(_program(), start_time=sim_time(0.0))
        results, final_time = result.value
        assert results == ["fast", "slow", "medium"]
        assert final_time == sim_time(5.0)

    def test_gather_preserves_order_not_completion_order(self) -> None:
        """Results match the order tasks were passed to Gather, not completion time."""

        @do
        def _worker(delay: float):
            yield Delay(delay)
            now = yield GetTime()
            return now

        @do
        def _program():
            t1 = yield Spawn(_worker(3.0))
            t2 = yield Spawn(_worker(1.0))
            t3 = yield Spawn(_worker(2.0))
            return (yield Gather(t1, t2, t3))

        result = _run_sim(_program(), start_time=sim_time(0.0))
        assert result.value == [sim_time(3.0), sim_time(1.0), sim_time(2.0)]


# ---------------------------------------------------------------------------
# Pattern 9: Chained delays in a single task (sequential time advancement)
#
# proboscis-ema:
#   yield sim_delay(1.0)
#   # do work
#   yield sim_delay(2.0)
#   # do more work
# ---------------------------------------------------------------------------


class TestSequentialDelays:
    def test_chained_delays_accumulate(self) -> None:
        @do
        def _program():
            timestamps = []
            for delay_s in [1.0, 2.0, 3.0, 4.0]:
                yield Delay(delay_s)
                timestamps.append((yield GetTime()))
            return timestamps

        result = _run_sim(_program(), start_time=sim_time(0.0))
        assert result.value == [sim_time(1.0), sim_time(3.0), sim_time(6.0), sim_time(10.0)]


# ---------------------------------------------------------------------------
# Pattern 10: Log formatting with sim time (proboscis-ema log_formatter)
#
# proboscis-ema uses log_formatter to stamp log messages with sim time.
# ---------------------------------------------------------------------------


class TestLogFormatterWithMultipleServices:
    def test_log_messages_carry_sim_time_from_different_tasks(self) -> None:
        @do
        def _service_a():
            yield Delay(1.0)
            yield Tell("service_a checkpoint")

        @do
        def _service_b():
            yield Delay(2.0)
            yield Tell("service_b checkpoint")

        @do
        def _program():
            t1 = yield Spawn(_service_a())
            t2 = yield Spawn(_service_b())
            yield Gather(t1, t2)
            return "done"

        result = run(
            Listen(
                WithHandler(
                    sim_time_handler(
                        start_time=sim_time(100.0),
                        log_formatter=lambda t, msg: f"[{sim_seconds(t):.0f}] {msg}",
                    ),
                    _program(),
                ),
            ),
            handlers=default_handlers(),
        )
        listen_result = result.value
        assert listen_result.value == "done"
        log = list(listen_result.log)
        assert "[101] service_a checkpoint" in log
        assert "[102] service_b checkpoint" in log


# ---------------------------------------------------------------------------
# Pattern 11: Full mini-backtest — the canonical proboscis-ema composition
#
# Combines: sim_time_handler + event_handler + core scheduler
# Multiple services running concurrently, events flowing between them.
# This is the ultimate migration-readiness test.
# ---------------------------------------------------------------------------


class TestFullMiniBacktest:
    def test_price_feed_strategy_execution_pipeline(self) -> None:
        """End-to-end mini-backtest:
        1. Price feed publishes MarketSignals at 1s intervals
        2. Strategy listens, spawns trade execution for each signal
        3. Trade executor delays 0.5s (simulating execution latency)
        4. Coordinator waits for all trades after feed stops
        """
        execution_log: list[str] = []

        @do
        def _price_feed(prices: list[float]):
            for price in prices:
                yield Delay(1.0)
                yield Publish(MarketSignal(symbol="BTC", price=price))
            yield Delay(1.0)
            yield Publish(BacktestStop())

        @do
        def _execute_trade(signal: MarketSignal):
            yield Delay(0.5)
            now = yield GetTime()
            record = f"FILL:{signal.symbol}@{signal.price}:t={sim_seconds(now):.1f}"
            execution_log.append(record)
            return record

        @do
        def _strategy():
            tasks = []
            while True:
                event = yield WaitForEvent(MarketSignal, BacktestStop)
                if isinstance(event, BacktestStop):
                    break
                task = yield Spawn(_execute_trade(event))
                tasks.append(task)
            results = yield Gather(*tasks) if tasks else []
            return results

        @do
        def _backtest():
            strategy = yield Spawn(_strategy())
            yield Spawn(_price_feed([100.0, 200.0, 300.0]))
            results = yield Wait(strategy)
            final_time = yield GetTime()
            return {"results": results, "final_time": final_time}

        result = _run_sim_events(_backtest(), start_time=sim_time(0.0))
        backtest = result.value
        assert len(backtest["results"]) == 3
        assert backtest["final_time"] == sim_time(4.0)
        assert len(execution_log) == 3

    def test_backtest_with_log_formatter_traces_execution(self) -> None:
        """Verify log formatter captures sim time across multiple services."""

        @do
        def _feed():
            yield Delay(1.0)
            yield Tell("signal:BTC@100")
            yield Publish(MarketSignal(symbol="BTC", price=100.0))
            yield Delay(1.0)
            yield Publish(BacktestStop())

        @do
        def _strategy():
            event = yield WaitForEvent(MarketSignal, BacktestStop)
            if isinstance(event, MarketSignal):
                yield Tell(f"trade:{event.symbol}")
                yield WaitForEvent(BacktestStop)
            return "done"

        @do
        def _backtest():
            strategy = yield Spawn(_strategy())
            yield Spawn(_feed())
            return (yield Wait(strategy))

        result = run(
            Listen(
                WithHandler(
                    sim_time_handler(
                        start_time=sim_time(0.0),
                        log_formatter=lambda t, msg: f"[t={sim_seconds(t):.0f}] {msg}",
                    ),
                    WithHandler(event_handler(), _backtest()),
                ),
            ),
            handlers=default_handlers(),
        )
        listen_result = result.value
        assert listen_result.value == "done"
        log = list(listen_result.log)
        assert "[t=1] signal:BTC@100" in log
        assert "[t=1] trade:BTC" in log
