"""Regression test: memo_rewriter + cache_handler + env breaks after move semantics change.

Reproduces the NoMatchingHandlerError seen in the Nakagawa pipeline:

    memo_rewriter delegates a CacheGetEffect, but the cache_handler
    cannot receive it because the handler chain is broken after the
    continuation handle ownership split (507be92e).

Minimal pattern:
    1. A custom effect (FetchData) that is memo-rewritten via CacheGet/CachePut
    2. A handler that resolves FetchData when the cache misses
    3. An interpreter that composes: [data_handler, memo_rewriter(FetchData), cache_handler]
    4. An env dict resolved through the CLI path

This mirrors the Nakagawa interpreter pattern:
    [yahoo_finance_price_handler, *memo_rewriters(HistoricalPriceQuery), sqlite_cache_handler]
"""

from __future__ import annotations

import doeff
from doeff import Ask, WithHandler, do
from doeff._types_internal import EffectBase
from doeff.handlers.cache_handlers import (
    cache_handler,
    in_memory_cache_handler,
    memo_rewriters,
)
from doeff.rust_vm import default_handlers, run
from doeff.storage import InMemoryStorage
from doeff.types import EffectGenerator, RunResult


class FetchData(EffectBase):
    """A custom effect that fetches data (analogous to HistoricalPriceQuery)."""

    def __init__(self, key: str):
        self.key = key

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FetchData) and self.key == other.key

    def __hash__(self) -> int:
        return hash(("FetchData", self.key))


@do
def fetch_data_handler(effect: FetchData, k: object):
    """Handler that resolves FetchData effects (analogous to yahoo_finance_price_handler)."""
    if not isinstance(effect, FetchData):
        yield doeff.Pass()
        return None
    # Simulate fetching data
    result = {"data": f"value_for_{effect.key}"}
    return (yield doeff.Resume(k, result))


@do
def program_that_fetches() -> EffectGenerator[dict]:
    """A program that yields a FetchData effect."""
    result = yield FetchData("test_key")
    return result


def _compose_handlers(program, *handlers):
    wrapped = program
    for handler in reversed(handlers):
        wrapped = WithHandler(handler, wrapped)
    return wrapped


def test_memo_rewriter_with_cache_handler_no_env():
    """Basic test: memo_rewriter + cache_handler works without env."""
    program = program_that_fetches()

    wrapped = _compose_handlers(
        program,
        fetch_data_handler,
        *memo_rewriters(FetchData),
        in_memory_cache_handler(),
    )

    result: RunResult = run(wrapped, handlers=default_handlers())
    assert result.is_ok(), f"Expected Ok, got error: {result.error}"
    assert result.value == {"data": "value_for_test_key"}


def test_memo_rewriter_with_cache_handler_and_ask():
    """Test: memo_rewriter + cache_handler + Ask effect resolved via env."""
    @do
    def program_with_ask() -> EffectGenerator[dict]:
        _config = yield Ask("some_config")
        result = yield FetchData("test_key")
        return result

    from doeff.effects import Local

    inner = program_with_ask()
    env = {"some_config": "config_value"}
    program = Local(env, inner)

    wrapped = _compose_handlers(
        program,
        fetch_data_handler,
        *memo_rewriters(FetchData),
        in_memory_cache_handler(),
    )

    result: RunResult = run(wrapped, handlers=default_handlers())
    assert result.is_ok(), f"Expected Ok, got error: {result.error}"
    assert result.value == {"data": "value_for_test_key"}


def test_memo_rewriter_cache_miss_then_delegate():
    """Test the full memo path: cache miss -> delegate to handler -> cache put.

    This is the exact path that breaks in the Nakagawa pipeline:
    1. memo_rewriter intercepts FetchData
    2. Tries CacheGet -> KeyError (cache miss)
    3. Delegates to fetch_data_handler via Delegate()
    4. Stores result via CachePut
    5. Resumes with the fetched value
    """
    storage = InMemoryStorage()

    @do
    def program() -> EffectGenerator[dict]:
        # First fetch: should miss cache, delegate, then cache
        result1 = yield FetchData("key_a")
        # Second fetch of same key: should hit cache
        result2 = yield FetchData("key_a")
        return {"first": result1, "second": result2}

    wrapped = _compose_handlers(
        program(),
        fetch_data_handler,
        *memo_rewriters(FetchData),
        cache_handler(storage),
    )

    result: RunResult = run(wrapped, handlers=default_handlers())
    assert result.is_ok(), f"Expected Ok, got error: {result.error}"
    assert result.value["first"] == {"data": "value_for_key_a"}
    assert result.value["second"] == {"data": "value_for_key_a"}


def test_memo_rewriter_cache_handler_with_local_env():
    """Full integration: Local(env) + memo_rewriter + cache_handler + external handler.

    This is the exact pattern from the Nakagawa interpreter that broke:
    ```python
    wrapped = _compose_handlers(
        p,  # p is Local(env, original_program) when called via doeff run
        yahoo_finance_price_handler(),
        *memo_rewriters(HistoricalPriceQuery),
        sqlite_cache_handler(_CACHE_PATH),
    )
    result = run(wrapped, handlers=default_handlers())
    ```
    """
    from doeff.effects import Local

    storage = InMemoryStorage()

    @do
    def program_with_ask_and_fetch() -> EffectGenerator[dict]:
        config = yield Ask("db_url")
        data = yield FetchData("key_b")
        return {"config": config, "data": data}

    env = {"db_url": "sqlite:///test.db"}
    local_program = Local(env, program_with_ask_and_fetch())

    wrapped = _compose_handlers(
        local_program,
        fetch_data_handler,
        *memo_rewriters(FetchData),
        cache_handler(storage),
    )

    result: RunResult = run(wrapped, handlers=default_handlers())
    assert result.is_ok(), (
        f"Expected Ok but got error: {result.error}\n"
        "This reproduces the NoMatchingHandlerError seen when memo_rewriter "
        "delegates CacheGetEffect but cache_handler cannot receive it."
    )
    assert result.value == {
        "config": "sqlite:///test.db",
        "data": {"data": "value_for_key_b"},
    }
