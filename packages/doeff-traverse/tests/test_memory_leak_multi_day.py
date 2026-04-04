"""Reproduce: memory leak when running traverse in a multi-day loop.

Production case: C_LLM pipeline runs 60 trading days sequentially.
Each day performs a traverse over ~95 items (daily factor computation),
where each item triggers ~24 LLM queries (all memo-HIT, returning instantly).

Observed: RSS grows ~280MB/day. At day 53/60, process reaches 17GB and OOMs.
Expected: each day's traverse should release its continuations after completing.
Memory should stay roughly constant across days.

Root cause hypothesis: doeff VM (Rust) retains continuation references
from completed traverse items, preventing Python GC from reclaiming them.
"""

import resource
import sys

import pytest

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from doeff import EffectBase, Pass, Program, Resume, do, run, slog
from doeff_vm import WithHandler
from doeff_core_effects import Ask
from doeff_core_effects.handlers import (
    await_handler,
    lazy_ask,
    listen_handler,
    local_handler,
    slog_handler,
    state,
    try_handler,
    writer,
)
from doeff_core_effects.scheduler import scheduled
from doeff_time import GetTime, WaitUntil
from doeff_time.handlers import sim_time_handler
from doeff_traverse.effects import Inspect, Traverse
from doeff_traverse.handlers import parallel, fail_handler

SIM_EPOCH = datetime(2025, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class MockLLMQuery(EffectBase):
    """Simulated LLM query effect (always returns cached result)."""
    key: str


@dataclass(frozen=True)
class MockCacheCheck(EffectBase):
    """Simulated cache check (always HIT)."""
    key: str


def _make_mock_handler():
    """Handler that simulates memo-HIT LLM queries and cache checks."""

    @do
    def _handler(effect, k):
        if isinstance(effect, MockLLMQuery):
            # Simulate memo HIT — return immediately
            return (yield Resume(k, {"score": 0.5, "key": effect.key}))
        elif isinstance(effect, MockCacheCheck):
            return (yield Resume(k, True))
        yield Pass(effect, k)

    return _handler


@do
def _single_item_program(item_id: int) -> Program[dict]:
    """Simulates one day's single-item factor computation.

    Each item: cache check → LLM query → slog → return.
    Mimics the per-symbol lead-lag estimation in production.
    """
    yield MockCacheCheck(key=f"item_{item_id}")
    result = yield MockLLMQuery(key=f"llm_{item_id}")
    yield slog(msg=f"item {item_id}: {result}")
    return result


@do
def _daily_traverse(n_items: int) -> Program[list]:
    """Simulates one day's factor computation via traverse.

    Production: traverse over 95 days × 24 symbols = many items.
    Each item triggers memo-HIT LLM query.
    """
    collection = yield Traverse(_single_item_program, range(n_items), label="daily_factor")
    items = yield Inspect(collection)
    results = [item.value for item in items if not item.failed]
    return results


@do
def _multi_day_program(n_days: int, n_items_per_day: int) -> Program[int]:
    """Simulates multi-day pipeline: loop over days, each running traverse.

    Production: 60 days × traverse(95 items × 24 symbols).
    This is where memory grows unbounded.
    """
    for day in range(n_days):
        yield slog(msg=f"Day {day + 1}/{n_days}")
        results = yield _daily_traverse(n_items_per_day)
        yield slog(msg=f"Day {day + 1} done: {len(results)} items")
    return n_days


def _compose(program, *handlers):
    wrapped = program
    for h in reversed(handlers):
        wrapped = WithHandler(h, wrapped)
    return wrapped


def _run_multi_day(n_days, n_items_per_day, concurrency=20):
    """Run multi-day traverse with production-like handler stack."""
    wrapped = _compose(
        _multi_day_program(n_days, n_items_per_day),
        lazy_ask(env={"config": "test"}),
        writer(),
        try_handler,
        state(),
        local_handler,
        listen_handler,
        await_handler(),
        sim_time_handler(start_time=SIM_EPOCH),
        _make_mock_handler(),
        parallel(concurrency=concurrency),
        fail_handler,
        slog_handler(),
    )
    return run(scheduled(wrapped))


def _get_rss_mb():
    """Get current max RSS in MB."""
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return maxrss / (1024 * 1024)  # bytes on macOS
    return maxrss / 1024  # KB on Linux


def test_multi_day_traverse_memory_constant():
    """Memory should stay roughly constant across days.

    Run 10 days of traverse (100 items each), measure RSS growth.
    If memory leaks, RSS will grow significantly per day.

    Pass criteria: RSS growth < 50MB over 10 days.
    (With leak: ~280MB/day × 10 = 2.8GB growth)
    """
    import gc

    gc.collect()
    rss_before = _get_rss_mb()

    result = _run_multi_day(n_days=10, n_items_per_day=100)
    assert result == 10

    gc.collect()
    rss_after = _get_rss_mb()
    growth = rss_after - rss_before

    # Generous threshold — 50MB for 10 days of traverse
    # With production leak rate (~280MB/day), this would be ~2.8GB
    assert growth < 50, (
        f"Memory grew {growth:.0f}MB over 10 days of traverse. "
        f"Expected < 50MB. Possible memory leak in doeff VM."
    )


def test_multi_day_traverse_30_days():
    """30 days × 100 items — matches production scale.

    Production: 30 days × 95 factor days × 24 symbols ≈ 30 × 100.
    Should complete without OOM on a 16GB machine.
    """
    import gc

    gc.collect()
    rss_before = _get_rss_mb()

    result = _run_multi_day(n_days=30, n_items_per_day=100)
    assert result == 30

    gc.collect()
    rss_after = _get_rss_mb()
    growth = rss_after - rss_before

    # 150MB for 30 days — generous but catches ~280MB/day leak
    assert growth < 150, (
        f"Memory grew {growth:.0f}MB over 30 days of traverse. "
        f"Expected < 150MB. Possible memory leak in doeff VM."
    )


@pytest.mark.slow
def test_multi_day_traverse_60_days_stress():
    """60 days × 200 items — stress test matching production failure.

    Production OOMs at day 53 with 17GB RSS.
    This test uses 200 items/day to amplify the leak.
    """
    import gc

    gc.collect()
    rss_before = _get_rss_mb()

    result = _run_multi_day(n_days=60, n_items_per_day=200)
    assert result == 60

    gc.collect()
    rss_after = _get_rss_mb()
    growth = rss_after - rss_before

    assert growth < 300, (
        f"Memory grew {growth:.0f}MB over 60 days × 200 items. "
        f"Expected < 300MB. Production OOMs at ~17GB after 53 days."
    )
