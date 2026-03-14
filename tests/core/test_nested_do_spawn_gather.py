"""
Regression: nested @do + Spawn+Gather + WithHandler raises TypeError.

When async_gather is called DIRECTLY, Spawn+Gather works.
When async_gather is called THROUGH a @do wrapper (like throttled_gather),
the same Spawn+Gather raises:
    TypeError: yielded value must be EffectBase or DoExpr

The wrapper does nothing special — just yields slog() then delegates to async_gather.
"""
import asyncio
from dataclasses import dataclass, field
from typing import Any, List

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
    default_handlers,
    do,
    run,
    slog,
)
from doeff.effects.base import Effect, EffectBase


# --- Custom effect + handler ---

@dataclass(frozen=True)
class FetchEffect(EffectBase):
    key: str


@do
def _query_fn(key: str) -> EffectGenerator[str]:
    result = yield Await(asyncio.to_thread(lambda: f"data-{key}"))
    return result


@dataclass
class Service:
    @do
    def fetch(self, key: str) -> EffectGenerator[str]:
        return (yield _query_fn(key))


def make_handler():
    @do
    def _handler(effect: Effect, k):
        if not isinstance(effect, FetchEffect):
            yield Pass()
            return
        svc = yield Ask("service")
        data = yield svc.fetch(effect.key)
        return (yield Resume(k, data))
    return _handler


# --- Pipeline pieces ---

@do
def fetch_one(key: str) -> EffectGenerator[Any]:
    result = yield Try(FetchEffect(key=key))
    if result.is_err():
        return f"err-{key}"
    return result.value


def wrap_sem(p, sem):
    @do
    def _w() -> EffectGenerator[Any]:
        yield AcquireSemaphore(sem)
        r = yield p
        yield ReleaseSemaphore(sem)
        return r
    return _w()


def my_async_gather(*programs):
    """Direct async_gather using Spawn+Gather."""
    @do
    def _g() -> EffectGenerator[list]:
        tasks = []
        for p in programs:
            t = yield Spawn(p, daemon=False)
            tasks.append(t)
        return list((yield Gather(*tasks)))
    return _g()


@do
def throttled_gather(
    *programs: Any, concurrency: int
) -> EffectGenerator[List[Any]]:
    """Wrapper that adds slog before delegating to async_gather."""
    semaphore = yield CreateSemaphore(concurrency)
    yield slog(msg="gathering", level="debug", concurrency=concurrency, total=len(programs))
    wrapped = [wrap_sem(p, semaphore) for p in programs]
    results = yield my_async_gather(*wrapped)
    yield slog(msg="gathered", level="debug")
    return list(results)


@do
def throttled_gather_with_progress(
    *programs: Any, concurrency: int, description: str = ""
) -> EffectGenerator[List[Any]]:
    """Another wrapper layer (like the real code)."""
    if description:
        yield slog(msg=description, level="info")
    return (yield throttled_gather(*programs, concurrency=concurrency))


# --- Tests ---

def _env():
    return {"service": Service()}


def test_direct_async_gather_works():
    """async_gather called directly with WithHandler — works."""
    @do
    def test() -> EffectGenerator[list]:
        sem = yield CreateSemaphore(2)
        programs = [wrap_sem(Try(fetch_one(f"k{i}")), sem) for i in range(3)]
        return (yield my_async_gather(*programs))

    wrapped = WithHandler(make_handler(), test())
    wrapped = Local(_env(), wrapped)
    r = run(wrapped, handlers=default_handlers())
    assert r.is_ok(), f"Failed: {r.error}"
    assert len(r.value) == 3


def test_throttled_gather_wrapper():
    """throttled_gather (@do wrapper around async_gather) with WithHandler."""
    @do
    def test() -> EffectGenerator[list]:
        programs = [Try(fetch_one(f"k{i}")) for i in range(3)]
        return (yield throttled_gather(*programs, concurrency=2))

    wrapped = WithHandler(make_handler(), test())
    wrapped = Local(_env(), wrapped)
    r = run(wrapped, handlers=default_handlers())
    assert r.is_ok(), f"Failed: {r.error}"
    assert len(r.value) == 3


def test_throttled_gather_with_progress_wrapper():
    """Double-nested @do wrapper around async_gather with WithHandler."""
    @do
    def test() -> EffectGenerator[list]:
        programs = [Try(fetch_one(f"k{i}")) for i in range(3)]
        return (yield throttled_gather_with_progress(
            *programs, concurrency=2, description="testing"
        ))

    wrapped = WithHandler(make_handler(), test())
    wrapped = Local(_env(), wrapped)
    r = run(wrapped, handlers=default_handlers())
    assert r.is_ok(), f"Failed: {r.error}"
    assert len(r.value) == 3
