"""
Regression guard for gh-321-downstream.

This captures the downstream proboscis-ema pattern that originally escaped the
smaller Spawn+Gather reproductions:

- custom effect handled via WithHandler + Ask from env
- nested @do service/cache chain
- multiple Await(asyncio.to_thread(...)) boundaries
- Try-wrapped worker programs
- throttled_gather wrapper around Spawn + Gather
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from doeff import (
    AcquireSemaphore,
    Ask,
    Await,
    CreateSemaphore,
    EffectGenerator,
    Gather,
    Local,
    Pass,
    ReleaseSemaphore,
    Resume,
    Spawn,
    Try,
    WithHandler,
    default_handlers,
    do,
    run,
    slog,
)
from doeff.effects._program_types import ProgramLike
from doeff.effects.base import Effect, EffectBase


@dataclass(frozen=True)
class FetchPriceEffect(EffectBase):
    ticker: str
    start: datetime
    end: datetime


@do
def _query_prices(
    *, ticker: str, start_time: datetime, end_time: datetime
) -> EffectGenerator[list[dict[str, object]]]:
    del start_time, end_time
    return (yield Await(asyncio.to_thread(list)))


@do
def _query_sync_status(
    *, ticker: str, start_time: datetime, end_time: datetime
) -> EffectGenerator[list[dict[str, object]]]:
    del ticker, start_time, end_time
    return (yield Await(asyncio.to_thread(list)))


@do
def _store_prices(data: list[dict[str, object]]) -> EffectGenerator[None]:
    yield Await(asyncio.to_thread(lambda: list(data)))


@do
def _store_sync_status(records: list[dict[str, object]]) -> EffectGenerator[None]:
    yield Await(asyncio.to_thread(lambda: list(records)))


@dataclass
class CacheStrategy:
    query_prices_fn: Callable = field(default=_query_prices)
    query_sync_status_fn: Callable = field(default=_query_sync_status)
    store_prices_fn: Callable = field(default=_store_prices)
    store_sync_status_fn: Callable = field(default=_store_sync_status)

    @do
    def get_cached(
        self, ticker: str, start: datetime, end: datetime
    ) -> EffectGenerator[list[dict[str, object]]]:
        cached = yield self.query_prices_fn(
            ticker=ticker,
            start_time=start,
            end_time=end,
        )
        yield self.query_sync_status_fn(
            ticker=ticker,
            start_time=start,
            end_time=end,
        )
        return cached

    @do
    def persist(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        rows: list[dict[str, object]],
    ) -> EffectGenerator[None]:
        yield self.store_prices_fn(rows)
        yield self.store_sync_status_fn(
            [
                {
                    "_time": start.isoformat(),
                    "ticker": ticker,
                    "window_end": end.isoformat(),
                    "is_synced": True,
                }
            ]
        )


@dataclass
class PriceService:
    cache: CacheStrategy = field(default_factory=CacheStrategy)

    @do
    def _fetch_from_api(
        self, ticker: str, start: datetime, end: datetime
    ) -> EffectGenerator[list[dict[str, object]]]:
        del start, end
        return (
            yield Await(
                asyncio.to_thread(
                    lambda: [{"ticker": ticker, "close": 101.5, "source": "api"}]
                )
            )
        )

    @do
    def fetch(
        self, ticker: str, start: datetime, end: datetime
    ) -> EffectGenerator[list[dict[str, object]]]:
        cached = yield self.cache.get_cached(ticker, start, end)
        if cached:
            return cached

        fetched = yield self._fetch_from_api(ticker, start, end)
        yield self.cache.persist(ticker, start, end, fetched)
        return fetched


def make_price_handler():
    @do
    def _handler(effect: Effect, k):
        if not isinstance(effect, FetchPriceEffect):
            yield Pass()
            return

        service = yield Ask("price_service")
        data = yield service.fetch(effect.ticker, effect.start, effect.end)
        return (yield Resume(k, data))

    return _handler


@do
def fetch_series(ticker: str) -> EffectGenerator[str]:
    start = datetime(2025, 9, 1, 12, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=3)
    result = yield Try(FetchPriceEffect(ticker=ticker, start=start, end=end))
    if result.is_err():
        raise RuntimeError(f"fetch failed for {ticker}: {result.error}")
    rows = result.value
    return str(rows[0]["ticker"])


@do
def compute_signal(ticker: str) -> EffectGenerator[str]:
    result = yield Try(fetch_series(ticker))
    if result.is_err():
        raise RuntimeError(f"compute failed for {ticker}: {result.error}")
    return result.value


def _async_gather(*programs: ProgramLike):
    @do
    def _run_gather() -> EffectGenerator[list[Any]]:
        tasks = []
        for program in programs:
            task = yield Spawn(program, daemon=False)
            tasks.append(task)
        return list((yield Gather(*tasks)))

    return _run_gather()


def _wrap_with_semaphore(program: ProgramLike, semaphore):
    @do
    def _wrapped() -> EffectGenerator[Any]:
        yield AcquireSemaphore(semaphore)
        try:
            return (yield program)
        finally:
            yield ReleaseSemaphore(semaphore)

    return _wrapped()


@do
def throttled_gather(
    *programs: ProgramLike,
    concurrency: int,
) -> EffectGenerator[list[Any]]:
    semaphore = yield CreateSemaphore(concurrency)
    yield slog(
        msg="gathering with downstream-style wrapper",
        level="debug",
        concurrency=concurrency,
        total=len(programs),
    )
    wrapped = [_wrap_with_semaphore(program, semaphore) for program in programs]
    results = yield _async_gather(*wrapped)
    return list(results)


@do
def throttled_gather_with_progress(
    *programs: ProgramLike,
    concurrency: int,
    description: str,
) -> EffectGenerator[list[Any]]:
    yield slog(msg=description, level="info", total=len(programs))
    return (yield throttled_gather(*programs, concurrency=concurrency))


def test_downstream_spawn_gather_service_chain() -> None:
    @do
    def program() -> EffectGenerator[list[Any]]:
        workers = [Try(compute_signal(f"SYM{i}")) for i in range(5)]
        return (
            yield throttled_gather_with_progress(
                *workers,
                concurrency=2,
                description="proboscis-style gather",
            )
        )

    wrapped = WithHandler(make_price_handler(), program())
    wrapped = Local({"price_service": PriceService()}, wrapped)

    result = run(wrapped, handlers=default_handlers())

    assert result.is_ok(), result.display()
    assert [item.value for item in result.value] == [f"SYM{i}" for i in range(5)]
