"""Bisect: which part of cache() causes O(N) memory blowup?

Tests isolate individual effects used by cache() to find the culprit.
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

import pytest

from doeff import (
    Gather,
    Spawn,
    default_handlers,
    do,
    run,
    slog,
)
from doeff.effects import Await
from doeff.effects.execution_context import GetExecutionContext
from doeff.effects.result import Try
from doeff.types import FrozenDict

TIMEOUT_SECONDS = 60


def _timeout_handler(signum: int, frame: Any) -> None:
    raise TimeoutError(f"Deadlock detected after {TIMEOUT_SECONDS}s")


def _run_with_timeout(program):
    old = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(TIMEOUT_SECONDS)
    try:
        return run(program, handlers=default_handlers())
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _get_rss_mb() -> float:
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


N = 500


@do
def _spawn_gather_n(factory, n: int):
    tasks = []
    for i in range(n):
        t = yield Spawn(factory(i), daemon=False)
        tasks.append(t)
    return list((yield Gather(*tasks)))


# --- Control: just Await, no extras ---
@do
def _bare_await(i: int):
    yield Await(asyncio.sleep(0.001))
    return f"done-{i}"


# --- Suspect 1: GetExecutionContext ---
@do
def _with_get_context(i: int):
    context = yield GetExecutionContext()
    _ = getattr(context, "active_chain", ())
    yield Await(asyncio.sleep(0.001))
    return f"done-{i}"


# --- Suspect 2: nested @do (like cache wrapper) ---
@do
def _with_nested_do(i: int):
    @do
    def inner():
        yield Await(asyncio.sleep(0.001))
        return f"done-{i}"
    return (yield inner())


# --- Suspect 3: Try wrapping ---
@do
def _with_try(i: int):
    @do
    def inner():
        yield Await(asyncio.sleep(0.001))
        return f"done-{i}"
    result = yield Try(inner())
    return result.value


# --- Suspect 4: cloudpickle.dumps ---
@do
def _with_pickle(i: int):
    import cloudpickle
    key = ("test_func", (i,), FrozenDict())
    cloudpickle.dumps(key)
    yield Await(asyncio.sleep(0.001))
    return f"done-{i}"


# --- Suspect 5: multiple nested @do (matching cache depth) ---
@do
def _cache_like_depth(i: int):
    """Mimics cache() nesting: wrapper -> build_key -> try_get -> compute_and_cache"""
    @do
    def build_key():
        return ("test", (i,), FrozenDict())

    @do
    def try_get(key):
        # Simulate cache miss
        @do
        def inner():
            raise KeyError("miss")
        r = yield Try(inner())
        return r

    @do
    def compute(key):
        yield slog(msg=f"compute {i}", level="DEBUG")
        yield Await(asyncio.sleep(0.001))
        return f"done-{i}"

    context = yield GetExecutionContext()
    key = yield build_key()
    miss = yield try_get(key)
    return (yield compute(key))


class TestMemoryBisect:
    def test_bare_await(self):
        """Control: bare Await. Should be fast and low memory."""
        rss_before = _get_rss_mb()
        r = _run_with_timeout(_spawn_gather_n(_bare_await, N))
        rss_after = _get_rss_mb()
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == N
        print(f"\n  bare_await: RSS {rss_before:.0f} -> {rss_after:.0f} MB (delta={rss_after-rss_before:.0f})")

    def test_get_context(self):
        """Suspect 1: GetExecutionContext"""
        rss_before = _get_rss_mb()
        r = _run_with_timeout(_spawn_gather_n(_with_get_context, N))
        rss_after = _get_rss_mb()
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == N
        print(f"\n  get_context: RSS {rss_before:.0f} -> {rss_after:.0f} MB (delta={rss_after-rss_before:.0f})")

    def test_nested_do(self):
        """Suspect 2: nested @do"""
        rss_before = _get_rss_mb()
        r = _run_with_timeout(_spawn_gather_n(_with_nested_do, N))
        rss_after = _get_rss_mb()
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == N
        print(f"\n  nested_do: RSS {rss_before:.0f} -> {rss_after:.0f} MB (delta={rss_after-rss_before:.0f})")

    def test_try_wrap(self):
        """Suspect 3: Try wrapping"""
        rss_before = _get_rss_mb()
        r = _run_with_timeout(_spawn_gather_n(_with_try, N))
        rss_after = _get_rss_mb()
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == N
        print(f"\n  try_wrap: RSS {rss_before:.0f} -> {rss_after:.0f} MB (delta={rss_after-rss_before:.0f})")

    def test_pickle(self):
        """Suspect 4: cloudpickle serialization"""
        rss_before = _get_rss_mb()
        r = _run_with_timeout(_spawn_gather_n(_with_pickle, N))
        rss_after = _get_rss_mb()
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == N
        print(f"\n  pickle: RSS {rss_before:.0f} -> {rss_after:.0f} MB (delta={rss_after-rss_before:.0f})")

    def test_cache_like_depth(self):
        """Suspect 5: full cache-like nesting depth"""
        rss_before = _get_rss_mb()
        r = _run_with_timeout(_spawn_gather_n(_cache_like_depth, N))
        rss_after = _get_rss_mb()
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == N
        print(f"\n  cache_like: RSS {rss_before:.0f} -> {rss_after:.0f} MB (delta={rss_after-rss_before:.0f})")
