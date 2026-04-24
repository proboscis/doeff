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


# Additional import for nested handler tests
from dataclasses import dataclass as _dc_import_check  # noqa: F401


class TestMemoRewriterWithSimTime:
    """Production-like stack: sim_time + parallel + memo + Spawn handler."""

