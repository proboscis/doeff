"""
Regression test: Spawn+Gather with WithIntercept + WithHandler + custom Effect + Await(asyncio.to_thread)

When a program uses Spawn+Gather inside a deep WithHandler stack that includes
WithIntercept, the Rust VM raises:
    TypeError: yielded value must be EffectBase or DoExpr

The same program works correctly WITHOUT WithIntercept.

Reproduction conditions:
- Custom EffectBase subclass handled by a WithHandler
- Handler resolves the effect by Ask()-ing a service, calling a @do method chain
- The @do method chain includes Await(asyncio.to_thread(...))
- Multiple tasks Spawned + Gathered with semaphore rate-limiting
- All wrapped in compose_handlers (nested WithHandler) + WithIntercept
"""
import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List

import pytest

from doeff import (
    Ask,
    Await,
    CreateSemaphore,
    AcquireSemaphore,
    ReleaseSemaphore,
    EffectGenerator,
    Gather,
    Local,
    Pass,
    Resume,
    Semaphore,
    Spawn,
    Try,
    WithHandler,
    WithIntercept,
    default_handlers,
    do,
    run,
    slog,
)
from doeff.effects.base import Effect, EffectBase


# --- Custom effect (simulates HistoricalPriceEffect) ---

@dataclass(frozen=True)
class FetchEffect(EffectBase):
    ticker: str
    start: datetime
    end: datetime


# --- Service layer with @do methods and Await(asyncio.to_thread) ---

@do
def _query_fn(
    *, ticker: str, start_time: datetime, end_time: datetime
) -> EffectGenerator[List[Dict]]:
    result = yield Await(asyncio.to_thread(lambda: [{"t": ticker, "v": 42}]))
    return result


@do
def _sync_fn(
    *, ticker: str, start_time: datetime, end_time: datetime
) -> EffectGenerator[List[Dict]]:
    return []


@dataclass
class CacheStrategy:
    query_fn: Callable = field(default=_query_fn)
    sync_fn: Callable = field(default=_sync_fn)

    @do
    def get_cached(
        self, ticker: str, start: datetime, end: datetime
    ) -> EffectGenerator[List[Dict]]:
        raw = yield self.query_fn(
            ticker=ticker, start_time=start, end_time=end
        )
        _sync = yield self.sync_fn(
            ticker=ticker, start_time=start, end_time=end
        )
        return raw


@dataclass
class PriceService:
    cache: CacheStrategy = field(default_factory=CacheStrategy)

    @do
    def fetch(
        self, ticker: str, start: datetime, end: datetime
    ) -> EffectGenerator[List[Dict]]:
        cached = yield self.cache.get_cached(ticker, start, end)
        if cached:
            return cached
        yield Await(asyncio.to_thread(time.sleep, 0.001))
        return [{"t": ticker, "v": 100}]


# --- Handler (simulates price_handler_from_env) ---

def make_handler():
    @do
    def _handler(effect: Effect, k):
        if not isinstance(effect, FetchEffect):
            yield Pass()
            return
        svc = yield Ask("price_service")
        data = yield svc.fetch(effect.ticker, effect.start, effect.end)
        return (yield Resume(k, data))

    return _handler


# --- Pipeline: nested Try + Spawn+Gather ---

@do
def fetch_series(
    ticker: str, start: datetime, end: datetime
) -> EffectGenerator[Any]:
    result = yield Try(FetchEffect(ticker=ticker, start=start, end=end))
    if result.is_err():
        raise RuntimeError(f"fetch failed: {result.error}")
    return result.value


@do
def compute_movement(ticker: str) -> EffectGenerator[Any]:
    now = datetime.now()
    df_result = yield Try(fetch_series(ticker, now, now))
    if df_result.is_err():
        raise RuntimeError(f"compute failed: {df_result.error}")
    return df_result.value


def wrap_sem(p, sem):
    @do
    def _w() -> EffectGenerator[Any]:
        yield AcquireSemaphore(sem)
        r = yield p
        yield ReleaseSemaphore(sem)
        return r

    return _w()


@do
def fetch_movements(
    n: int, concurrency: int = 3
) -> EffectGenerator[list]:
    sem = yield CreateSemaphore(concurrency)
    programs = [
        wrap_sem(Try(compute_movement(f"SYM{i}")), sem) for i in range(n)
    ]
    tasks = []
    for p in programs:
        t = yield Spawn(p, daemon=False)
        tasks.append(t)
    return list((yield Gather(*tasks)))


# --- Helpers ---

def compose_handlers(program, *handlers):
    result = program
    for handler in reversed(handlers):
        result = WithHandler(handler, result)
    return result


@do
def passthrough_interceptor(effect: Effect):
    """Minimal interceptor that passes through all effects unchanged."""
    return effect


# --- Tests ---

def _build_program(n: int = 5, concurrency: int = 3, with_intercept: bool = False):
    svc = PriceService()
    env = {"price_service": svc}

    wrapped = compose_handlers(
        fetch_movements(n, concurrency=concurrency),
        make_handler(),
    )
    if with_intercept:
        wrapped = WithIntercept(passthrough_interceptor, wrapped)
    return Local(env, wrapped)


def test_spawn_gather_handler_without_intercept():
    """Works: Spawn+Gather + custom handler WITHOUT WithIntercept."""
    program = _build_program(n=5, with_intercept=False)
    result = run(program, handlers=default_handlers())
    assert result.is_ok(), f"Failed: {result.error}"
    assert len(result.value) == 5


def test_spawn_gather_handler_with_intercept():
    """Fails: same program WITH WithIntercept wrapping."""
    program = _build_program(n=5, with_intercept=True)
    result = run(program, handlers=default_handlers())
    assert result.is_ok(), f"Failed: {result.error}"
    assert len(result.value) == 5
