"""Reproducer: memo_rewriter + handler with Spawn/Wait → continuation already consumed.

When memo_rewriter delegates (cache MISS) to a handler that uses Spawn/Wait
internally, the final Resume(k, value) in memo_rewriter crashes with:
    "Resume: continuation already consumed"

This matches the production bug in proboscis-ema where:
    memo_rewriter(HistoricalPriceQuery) → ohlc_cached_handler → Spawn(Try(yfinance))
    → Wait → Resume → memo STORED → Resume(k, value) → CRASH

See: ISSUE-INF-021 in proboscis-ema
"""
from __future__ import annotations

from dataclasses import dataclass

from doeff import (
    EffectBase,
    EffectGenerator,
    Pass,
    Resume,
    WithHandler,
    do,
    run,
)
from doeff_core_effects import slog, Try
from doeff_core_effects.memo_handlers import (
    in_memory_memo_handler,
    make_memo_rewriter,
)
from doeff_core_effects.handlers import (
    await_handler,
    slog_handler,
    state,
    try_handler,
    writer,
)
from doeff_core_effects.scheduler import Spawn, Wait, scheduled
from doeff_time import GetTime
from doeff_time.handlers import sim_time_handler
from doeff_traverse.handlers import parallel, fail_handler


# --- Custom effect (analogous to HistoricalPriceQuery) ---

@dataclass(frozen=True)
class FetchOHLC(EffectBase):
    ticker: str
    date: str


# --- Handler that uses Spawn/Wait (analogous to ohlc_cached_handler) ---

@do
def spawn_fetch_handler(effect, k):
    """Handler that resolves FetchOHLC by Spawning a worker task.

    This mirrors ohlc_cached_handler.update() which does:
        task = yield Spawn(Try(_fetch_and_save_range(...)))
        result = yield Wait(task)
    """
    if not isinstance(effect, FetchOHLC):
        yield Pass(effect, k)
        return

    # Simulate the Spawn/Wait pattern from ohlc_cached_handler.update()
    @do
    def _worker():
        yield slog(msg=f"worker: fetching {effect.ticker} {effect.date}")
        return {"close": 1234.5, "ticker": effect.ticker, "date": effect.date}

    task = yield Spawn(Try(_worker()))
    result = yield Wait(task)
    if result.is_err():
        raise result.error

    yield slog(msg=f"handler: got {result.value}")
    return (yield Resume(k, result.value))


# --- Simple handler WITHOUT Spawn (control) ---

@do
def simple_fetch_handler(effect, k):
    """Handler that resolves FetchOHLC synchronously (no Spawn)."""
    if not isinstance(effect, FetchOHLC):
        yield Pass(effect, k)
        return
    value = {"close": 1234.5, "ticker": effect.ticker, "date": effect.date}
    return (yield Resume(k, value))


# --- Test helpers ---

def _compose(program, *handlers):
    wrapped = program
    for h in reversed(handlers):
        wrapped = WithHandler(h, wrapped)
    return wrapped


def _run_scheduled(program):
    return run(scheduled(program))


# --- Tests ---

class TestMemoRewriterSpawnContinuation:
    """Test that memo_rewriter + Spawn/Wait handler doesn't break continuations."""

    def test_simple_handler_no_memo(self):
        """Control: simple handler without memo works."""
        @do
        def prog() -> EffectGenerator[dict]:
            return (yield FetchOHLC(ticker="8801.T", date="2026-04-09"))

        result = _run_scheduled(_compose(
            prog(),
            writer(),
            try_handler,
            state(),
            await_handler(),
            slog_handler(),
            simple_fetch_handler,
        ))
        assert result == {"close": 1234.5, "ticker": "8801.T", "date": "2026-04-09"}

    def test_spawn_handler_no_memo(self):
        """Control: Spawn/Wait handler without memo works."""
        @do
        def prog() -> EffectGenerator[dict]:
            return (yield FetchOHLC(ticker="8801.T", date="2026-04-09"))

        result = _run_scheduled(_compose(
            prog(),
            writer(),
            try_handler,
            state(),
            await_handler(),
            slog_handler(),
            spawn_fetch_handler,
        ))
        assert result == {"close": 1234.5, "ticker": "8801.T", "date": "2026-04-09"}

    def test_memo_rewriter_with_simple_handler(self):
        """Control: memo_rewriter + simple handler works."""
        @do
        def prog() -> EffectGenerator[tuple]:
            first = yield FetchOHLC(ticker="8801.T", date="2026-04-09")
            second = yield FetchOHLC(ticker="8801.T", date="2026-04-09")
            return first, second

        rewriter = make_memo_rewriter(FetchOHLC)
        result = _run_scheduled(_compose(
            prog(),
            writer(),
            try_handler,
            state(),
            await_handler(),
            slog_handler(),
            in_memory_memo_handler(),
            simple_fetch_handler,
            rewriter,
        ))
        assert result[0] == {"close": 1234.5, "ticker": "8801.T", "date": "2026-04-09"}
        assert result[0] == result[1]  # second should be cache hit

    def test_memo_rewriter_with_spawn_handler_single_call(self):
        """BUG REPRODUCER: memo_rewriter MISS + Spawn/Wait handler → continuation consumed.

        This is the minimal reproduction of ISSUE-INF-021.
        """
        @do
        def prog() -> EffectGenerator[dict]:
            return (yield FetchOHLC(ticker="8801.T", date="2026-04-09"))

        rewriter = make_memo_rewriter(FetchOHLC)
        result = _run_scheduled(_compose(
            prog(),
            writer(),
            try_handler,
            state(),
            await_handler(),
            slog_handler(),
            in_memory_memo_handler(),
            spawn_fetch_handler,
            rewriter,
        ))
        assert result == {"close": 1234.5, "ticker": "8801.T", "date": "2026-04-09"}

    def test_memo_rewriter_with_spawn_handler_two_calls(self):
        """Extended: two FetchOHLC calls — first MISS, second HIT."""
        @do
        def prog() -> EffectGenerator[tuple]:
            first = yield FetchOHLC(ticker="8801.T", date="2026-04-09")
            second = yield FetchOHLC(ticker="8801.T", date="2026-04-09")
            return first, second

        rewriter = make_memo_rewriter(FetchOHLC)
        result = _run_scheduled(_compose(
            prog(),
            writer(),
            try_handler,
            state(),
            await_handler(),
            slog_handler(),
            in_memory_memo_handler(),
            spawn_fetch_handler,
            rewriter,
        ))
        assert result[0] == {"close": 1234.5, "ticker": "8801.T", "date": "2026-04-09"}
        assert result[0] == result[1]

    def test_memo_rewriter_with_spawn_handler_different_keys(self):
        """Two different FetchOHLC keys — both MISS."""
        @do
        def prog() -> EffectGenerator[tuple]:
            first = yield FetchOHLC(ticker="8306.T", date="2026-04-09")
            second = yield FetchOHLC(ticker="8801.T", date="2026-04-09")
            return first, second

        rewriter = make_memo_rewriter(FetchOHLC)
        result = _run_scheduled(_compose(
            prog(),
            writer(),
            try_handler,
            state(),
            await_handler(),
            slog_handler(),
            in_memory_memo_handler(),
            spawn_fetch_handler,
            rewriter,
        ))
        assert result[0]["ticker"] == "8306.T"
        assert result[1]["ticker"] == "8801.T"


# Additional import for nested handler tests
from dataclasses import dataclass as _dc_import_check  # noqa: F401


class TestMemoRewriterWithSimTime:
    """Production-like stack: sim_time + parallel + memo + Spawn handler."""

    def test_spawn_handler_with_gettime_inside(self):
        """Handler does GetTime before Spawn — matches ohlc_cached_handler.fetch()."""
        @do
        def spawn_handler_with_gettime(effect, k):
            if not isinstance(effect, FetchOHLC):
                yield Pass(effect, k)
                return
            # fetch() does GetTime for validation — like ohlc_cached_handler
            now = yield GetTime()
            yield slog(msg=f"handler: time={now}, fetching {effect.ticker}")

            @do
            def _worker():
                yield slog(msg=f"worker: {effect.ticker}")
                return {"close": 999.0, "ticker": effect.ticker}

            task = yield Spawn(Try(_worker()))
            result = yield Wait(task)
            if result.is_err():
                raise result.error
            return (yield Resume(k, result.value))

        @do
        def prog() -> EffectGenerator[dict]:
            return (yield FetchOHLC(ticker="8801.T", date="2026-04-09"))

        from datetime import datetime, timezone
        start = datetime(2026, 4, 9, 6, 0, tzinfo=timezone.utc)

        rewriter = make_memo_rewriter(FetchOHLC)
        # Handler ordering matches production: outer→inner
        # sim_time ABOVE spawn_handler (so GetTime inside handler is resolved)
        # memo_rewriter BELOW spawn_handler (so FetchOHLC is intercepted first)
        result = _run_scheduled(_compose(
            prog(),
            writer(),
            try_handler,
            state(),
            await_handler(),
            sim_time_handler(start_time=start),
            slog_handler(),
            in_memory_memo_handler(),
            spawn_handler_with_gettime,
            parallel(concurrency=10),
            fail_handler,
            slog_handler(),
            rewriter,
        ))
        assert result == {"close": 999.0, "ticker": "8801.T"}

    def test_full_production_pattern(self):
        """Full production pattern: sim_time + parallel + memo + Spawn/GetTime handler.
        Two sequential MISS calls."""
        @do
        def spawn_handler_with_gettime(effect, k):
            if not isinstance(effect, FetchOHLC):
                yield Pass(effect, k)
                return
            now = yield GetTime()
            yield slog(msg=f"fetch: {effect.ticker} at {now}")

            @do
            def _worker():
                yield slog(msg=f"yfinance: {effect.ticker}")
                return {"close": 1234.5, "ticker": effect.ticker}

            task = yield Spawn(Try(_worker()))
            result = yield Wait(task)
            if result.is_err():
                raise result.error
            return (yield Resume(k, result.value))

        @do
        def prog() -> EffectGenerator[tuple]:
            first = yield FetchOHLC(ticker="8306.T", date="2026-04-09")
            second = yield FetchOHLC(ticker="8801.T", date="2026-04-09")
            return first, second

        from datetime import datetime, timezone
        start = datetime(2026, 4, 9, 6, 0, tzinfo=timezone.utc)

        rewriter = make_memo_rewriter(FetchOHLC)
        result = _run_scheduled(_compose(
            prog(),
            writer(),
            try_handler,
            state(),
            await_handler(),
            sim_time_handler(start_time=start),
            slog_handler(),
            in_memory_memo_handler(),
            spawn_handler_with_gettime,
            parallel(concurrency=40),
            fail_handler,
            slog_handler(),
            rewriter,
        ))
        assert result[0]["ticker"] == "8306.T"
        assert result[1]["ticker"] == "8801.T"  # end of full_production_pattern

    def test_nested_handler_performs_fetch(self):
        """Nested handler: OrderFx handler internally performs FetchOHLC → Spawn.

        Mirrors paper_account_handler which handles MarketOrder and internally
        performs HistoricalPriceQuery via fetch_sim_fill_price.
        """
        @dataclass(frozen=True)
        class OrderFx(EffectBase):
            ticker: str

        @do
        def order_handler(effect, k):
            if not isinstance(effect, OrderFx):
                yield Pass(effect, k)
                return
            yield slog(msg=f"order: filling {effect.ticker}")
            ohlc = yield FetchOHLC(ticker=effect.ticker, date="2026-04-09")
            return (yield Resume(k, ohlc["close"]))

        @do
        def spawn_ohlc(effect, k):
            if not isinstance(effect, FetchOHLC):
                yield Pass(effect, k)
                return
            now = yield GetTime()

            @do
            def _w():
                yield slog(msg=f"yf: {effect.ticker}")
                return {"close": 7777.0, "ticker": effect.ticker}

            t = yield Spawn(Try(_w()))
            r = yield Wait(t)
            if r.is_err():
                raise r.error
            return (yield Resume(k, r.value))

        @do
        def prog() -> EffectGenerator[tuple]:
            a = yield OrderFx(ticker="8306.T")
            b = yield OrderFx(ticker="8801.T")
            return a, b

        from datetime import datetime, timezone
        start = datetime(2026, 4, 9, 6, 0, tzinfo=timezone.utc)
        rewriter = make_memo_rewriter(FetchOHLC)
        result = _run_scheduled(_compose(
            prog(),
            writer(), try_handler, state(), await_handler(),
            sim_time_handler(start_time=start),
            slog_handler(), in_memory_memo_handler(),
            spawn_ohlc,
            parallel(concurrency=40), fail_handler, slog_handler(),
            rewriter, order_handler,
        ))
        assert result == (7777.0, 7777.0)
