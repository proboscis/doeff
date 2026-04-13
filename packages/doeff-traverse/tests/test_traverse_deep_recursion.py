"""Reproduce: deep handler recursion with large parallel traverse.

When running a parallel traverse over ~300 items where each item
performs multiple effects, the handler chain recurses deeply,
eventually hitting stack limits or producing very slow execution.

Production case: compute_rolling_factors with 300 days of LLM factor
computation via parallel traverse. Traceback showed hundreds of
repeated handler frames, suggesting O(N) nested handler invocations.

Expected: traverse over 300+ items should work without deep recursion.
"""

import pytest

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from doeff import EffectBase, Pass, Program, Resume, do, run, slog
from doeff_vm import WithHandler
from doeff_core_effects import Ask, Get, Put
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
from doeff_traverse.effects import Inspect, Traverse
from doeff_traverse.handlers import parallel, sequential, fail_handler
from doeff_time import GetTime, WaitUntil, sim_time_handler


@do
def process_item(i: int) -> Program[int]:
    """Simple item processor that performs effects."""
    yield slog(msg=f"processing {i}")
    return i * 2


@do
def process_item_with_state(i: int) -> Program[int]:
    """Item processor that reads/writes state (more effect round-trips)."""
    count = yield Get("counter")
    yield Put("counter", (count or 0) + 1)
    yield slog(msg=f"item {i}, count={(count or 0) + 1}")
    return i * 2


@do
def process_item_multi_effect(i: int) -> Program[int]:
    """Item processor with 3+ effect round-trips per item."""
    yield slog(msg=f"start {i}")
    val = yield Ask("multiplier")
    yield slog(msg=f"multiply {i} by {val}")
    result = i * val
    yield slog(msg=f"done {i} = {result}")
    return result


def _compose(program, *handlers):
    wrapped = program
    for handler in reversed(handlers):
        wrapped = WithHandler(handler, wrapped)
    return wrapped


def _run_parallel(program, concurrency=40):
    wrapped = _compose(
        program,
        lazy_ask(env={"multiplier": 3}),
        writer(),
        try_handler,
        state(),
        local_handler,
        listen_handler,
        await_handler(),
        parallel(concurrency=concurrency),
        fail_handler,
        slog_handler(),
    )
    return run(scheduled(wrapped))


def _make_traverse_program(n, item_fn):
    @do
    def prog() -> Program[list]:
        collection = yield Traverse(item_fn, range(n), label="test")
        items = yield Inspect(collection)
        return [item.value for item in items if not item.failed]
    return prog()


# --- Tests ---

def test_traverse_50_items():
    """50 items — baseline."""
    result = _run_parallel(_make_traverse_program(50, process_item))
    assert len(result) == 50
    assert result[0] == 0
    assert result[49] == 98


def test_traverse_100_items():
    """100 items."""
    result = _run_parallel(_make_traverse_program(100, process_item))
    assert len(result) == 100


def test_traverse_300_items():
    """300 items — production failure case.

    compute_rolling_factors traverses ~300 days where each day runs
    news fetch + LLM scoring + symbol estimation (multiple effects per item).
    """
    result = _run_parallel(_make_traverse_program(300, process_item))
    assert len(result) == 300


def test_traverse_500_items():
    """500 items — stress test."""
    result = _run_parallel(_make_traverse_program(500, process_item))
    assert len(result) == 500


def test_traverse_300_with_state():
    """300 items each reading/writing state."""
    result = _run_parallel(_make_traverse_program(300, process_item_with_state))
    assert len(result) == 300


def test_traverse_300_multi_effect():
    """300 items with 3+ effect round-trips each.

    Closest to production: each traverse item performs multiple
    Ask/slog effects, creating many handler chain traversals per item.
    """
    result = _run_parallel(_make_traverse_program(300, process_item_multi_effect))
    assert len(result) == 300
    assert result[0] == 0
    assert result[1] == 3  # 1 * 3


# --- Tests with sim_time_handler (reproduces production stack overflow) ---
#
# Production case: compute_rolling_factors traverses ~300 days where each day
# runs news fetch + LLM scoring (multiple effects per item). When
# sim_time_handler is in the handler stack, the handler chain recursion depth
# grows with the number of items, eventually causing stack overflow or extreme
# slowdown.
#
# Without sim_time_handler, the same traverse completes fine (tests above pass).
# The interaction between traverse's parallel handler and sim_time_handler's
# continuation management is the root cause.

SIM_EPOCH = datetime(2025, 1, 1, tzinfo=timezone.utc)


@do
def process_item_with_time(i: int) -> Program[int]:
    """Item processor that uses GetTime (requires sim_time_handler)."""
    now = yield GetTime()
    yield slog(msg=f"processing {i} at {now}")
    return i * 2


@do
def process_item_time_multi_effect(i: int) -> Program[int]:
    """Item processor with GetTime + Ask + slog (3+ effects, requires sim_time)."""
    now = yield GetTime()
    val = yield Ask("multiplier")
    yield slog(msg=f"item {i} at {now}, multiply by {val}")
    result = i * val
    yield slog(msg=f"done {i} = {result}")
    return result


@do
def process_item_time_waituntil(i: int) -> Program[int]:
    """Item processor that waits (simulates daily schedule pattern).

    Each item gets a unique target time so sim_time_handler must advance
    the clock for each — this is the closest to the production pattern
    where each day's LLM computation happens at a different sim time.
    """
    now = yield GetTime()
    target = now + timedelta(seconds=i)
    yield WaitUntil(target)
    yield slog(msg=f"item {i} after wait")
    return i * 2


def _run_parallel_with_sim_time(program, concurrency=40):
    """Run with sim_time_handler in the handler stack.

    Handler ordering matches production:
      lazy_ask → core → sim_time → domain (parallel, fail, slog)
    """
    wrapped = _compose(
        program,
        lazy_ask(env={"multiplier": 3}),
        writer(),
        try_handler,
        state(),
        local_handler,
        listen_handler,
        await_handler(),
        sim_time_handler(start_time=SIM_EPOCH),
        parallel(concurrency=concurrency),
        fail_handler,
        slog_handler(),
    )
    return run(scheduled(wrapped))


def test_traverse_50_with_sim_time():
    """50 items with sim_time_handler — baseline."""
    result = _run_parallel_with_sim_time(
        _make_traverse_program(50, process_item_with_time)
    )
    assert len(result) == 50


def test_traverse_100_with_sim_time():
    """100 items with sim_time_handler."""
    result = _run_parallel_with_sim_time(
        _make_traverse_program(100, process_item_with_time)
    )
    assert len(result) == 100


def test_traverse_300_with_sim_time():
    """300 items with sim_time_handler — production failure case.

    This is the exact scenario that causes stack overflow in production:
    compute_rolling_factors traverses ~300 days, each performing GetTime +
    multiple LLM/news effects, with sim_time_handler in the handler stack.
    """
    result = _run_parallel_with_sim_time(
        _make_traverse_program(300, process_item_with_time)
    )
    assert len(result) == 300


def test_traverse_300_multi_effect_with_sim_time():
    """300 items × 3+ effects each + sim_time_handler.

    Closest reproduction of production: each traverse item performs
    GetTime + Ask + slog effects with sim_time_handler active.
    """
    result = _run_parallel_with_sim_time(
        _make_traverse_program(300, process_item_time_multi_effect)
    )
    assert len(result) == 300
    assert result[0] == 0
    assert result[1] == 3


def test_traverse_100_waituntil_with_sim_time():
    """100 items each doing WaitUntil + sim_time_handler.

    Each item advances the sim clock — tests that sim_time_handler
    correctly handles many concurrent WaitUntil from traverse workers.
    """
    result = _run_parallel_with_sim_time(
        _make_traverse_program(100, process_item_time_waituntil)
    )
    assert len(result) == 100


def test_traverse_300_waituntil_with_sim_time():
    """300 items × WaitUntil — stress test for sim_time + traverse.

    This is the most demanding test: 300 concurrent traverse items,
    each issuing WaitUntil to different sim times, forcing the
    sim_time_handler to manage 300 pending continuations.
    """
    result = _run_parallel_with_sim_time(
        _make_traverse_program(300, process_item_time_waituntil)
    )
    assert len(result) == 300


# --- Tests with deep handler stack (reproduces production handler depth) ---
#
# Production handler stack has 15+ handlers between the program and the
# scheduler. Each effect round-trip traverses the full handler chain.
# With N items × M effects/item × D handler depth, the total continuation
# depth grows as O(N × M × D), eventually causing stack overflow.


def _make_passthrough_handler(tag: str):
    """Create a handler that passes all effects through.

    These simulate the domain handlers (openai, yahoo_finance, news, cache, etc.)
    that exist in production but don't handle the test effects — they just add
    handler chain depth.
    """

    @do
    def _handler(effect, k):
        yield Pass(effect, k)

    return _handler


def _run_deep_stack(program, n_extra_handlers=15, concurrency=40):
    """Run with sim_time + many passthrough handlers to simulate production depth.

    Production handler stack (from cllm_interpreter.hy):
      lazy_ask → writer → try → state → local → listen → await
      → sim_time
      → sqlite_cache → openai → yahoo_finance → news_minio → news_polygon
      → sim_exchange → margin → cache → slack → parallel → fail → slog
      → memo_rewriters (×4)

    That's ~20+ handlers. Each effect in a traverse item must traverse
    the full chain on every yield. This test simulates that depth.
    """
    extra = [_make_passthrough_handler(f"extra_{i}") for i in range(n_extra_handlers)]
    wrapped = _compose(
        program,
        lazy_ask(env={"multiplier": 3}),
        writer(),
        try_handler,
        state(),
        local_handler,
        listen_handler,
        await_handler(),
        sim_time_handler(start_time=SIM_EPOCH),
        *extra,
        parallel(concurrency=concurrency),
        fail_handler,
        slog_handler(),
    )
    return run(scheduled(wrapped))


def test_traverse_100_deep_stack():
    """100 items × GetTime + deep handler stack (15 extra handlers)."""
    result = _run_deep_stack(
        _make_traverse_program(100, process_item_with_time),
        n_extra_handlers=15,
    )
    assert len(result) == 100


def test_traverse_300_deep_stack():
    """300 items × GetTime + deep handler stack (15 extra handlers).

    This is the closest reproduction of the production failure:
    - 300 items (= 300 trading days in compute_rolling_factors)
    - GetTime effect per item (sim_time_handler active)
    - 15 passthrough handlers (simulating domain handler chain depth)
    - parallel traverse with concurrency=40
    """
    result = _run_deep_stack(
        _make_traverse_program(300, process_item_with_time),
        n_extra_handlers=15,
    )
    assert len(result) == 300


def test_traverse_300_multi_effect_deep_stack():
    """300 items × 3+ effects × deep handler stack.

    Each item does GetTime + Ask + 2×slog = 4 effect round-trips,
    each traversing 20+ handlers. Total continuation depth ~ 300 × 4 × 20.
    """
    result = _run_deep_stack(
        _make_traverse_program(300, process_item_time_multi_effect),
        n_extra_handlers=15,
    )
    assert len(result) == 300
    assert result[1] == 3


def test_traverse_500_multi_effect_deep_stack():
    """500 items × 3+ effects × deep handler stack — stress test."""
    result = _run_deep_stack(
        _make_traverse_program(500, process_item_time_multi_effect),
        n_extra_handlers=15,
    )
    assert len(result) == 500


def test_traverse_300_very_deep_stack():
    """300 items × GetTime + 30 extra handlers — extreme depth.

    Tests whether handler chain depth alone causes issues at scale.
    """
    result = _run_deep_stack(
        _make_traverse_program(300, process_item_with_time),
        n_extra_handlers=30,
    )
    assert len(result) == 300


# --- Tests with catching handlers (simulate real domain handler behavior) ---
#
# Production handlers don't just pass through — they catch specific effects
# and Resume with values. This creates longer continuation chains because
# each Resume re-enters the handler stack. The passthrough handlers above
# don't capture this pattern.


@dataclass(frozen=True)
class DomainQueryEffect(EffectBase):
    """Simulated domain effect (like LLMStructuredQuery or FetchNews)."""
    key: str


@dataclass(frozen=True)
class CacheCheckEffect(EffectBase):
    """Simulated cache check (like sqlite_cache memo check)."""
    key: str


@do
def process_item_domain(i: int) -> Program[int]:
    """Item that performs domain effects (simulating LLM + news + cache per day).

    Each item:
    1. GetTime (handled by sim_time)
    2. CacheCheck (handled by cache handler — Resume)
    3. DomainQuery (handled by domain handler — Resume)
    4. slog (handled by slog handler)

    4 effects × handler catching = deeper continuation chain per item.
    """
    now = yield GetTime()
    cached = yield CacheCheckEffect(key=f"item_{i}")
    if cached is None:
        result = yield DomainQueryEffect(key=f"item_{i}")
    else:
        result = cached
    yield slog(msg=f"item {i} = {result}")
    return result


def _make_catching_handler(effect_type, response_fn):
    """Handler that catches a specific effect type and resumes with a value.

    This simulates real handlers (cache, openai, etc.) that intercept
    effects, do work, and Resume — creating continuation chain depth.
    """

    @do
    def _handler(effect, k):
        if isinstance(effect, effect_type):
            result = response_fn(effect)
            return (yield Resume(k, result))
        yield Pass(effect, k)

    return _handler


def _run_deep_catching_stack(program, concurrency=40):
    """Run with sim_time + catching handlers that actually Resume.

    Simulates production handler stack where:
    - cache handler catches CacheCheckEffect → Resume(None) (cache miss)
    - domain handler catches DomainQueryEffect → Resume(computed_value)
    - 10 passthrough handlers for additional depth
    """
    cache_handler = _make_catching_handler(
        CacheCheckEffect, lambda e: None  # always cache miss
    )
    domain_handler = _make_catching_handler(
        DomainQueryEffect, lambda e: hash(e.key) % 100  # deterministic result
    )
    extra = [_make_passthrough_handler(f"extra_{i}") for i in range(10)]

    wrapped = _compose(
        program,
        lazy_ask(env={"multiplier": 3}),
        writer(),
        try_handler,
        state(),
        local_handler,
        listen_handler,
        await_handler(),
        sim_time_handler(start_time=SIM_EPOCH),
        cache_handler,
        domain_handler,
        *extra,
        parallel(concurrency=concurrency),
        fail_handler,
        slog_handler(),
    )
    return run(scheduled(wrapped))


def test_traverse_100_catching_handlers():
    """100 items × domain effects + catching handlers."""
    result = _run_deep_catching_stack(
        _make_traverse_program(100, process_item_domain)
    )
    assert len(result) == 100


def test_traverse_300_catching_handlers():
    """300 items × domain effects + catching handlers.

    Closest to production: 300 items each performing 4 effects
    (GetTime, CacheCheck, DomainQuery, slog) with handlers that
    actually catch and Resume, plus 10 passthrough handlers for depth.
    """
    result = _run_deep_catching_stack(
        _make_traverse_program(300, process_item_domain)
    )
    assert len(result) == 300


def test_traverse_500_catching_handlers():
    """500 items × domain effects + catching handlers — stress test."""
    result = _run_deep_catching_stack(
        _make_traverse_program(500, process_item_domain)
    )
    assert len(result) == 500
