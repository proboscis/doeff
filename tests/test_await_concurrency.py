"""Test: are Await effects actually concurrent under Spawn+Gather?

If N tasks each sleep 1s and concurrency is unlimited, wall-clock time
should be ~1s (parallel), not ~Ns (serial).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from doeff import (
    AcquireSemaphore,
    CreateSemaphore,
    Gather,
    ReleaseSemaphore,
    Spawn,
    default_handlers,
    do,
    run,
)
from doeff import Await


from doeff import cache, WithHandler
# REMOVED: from doeff_core_effects.handlers import sqlite_cache_handler


async def _async_sleep(duration: float) -> str:
    await asyncio.sleep(duration)
    return "done"


@do
def _sleep_task(i: int, duration: float):
    result = yield Await(_async_sleep(duration))
    return f"{i}:{result}"


@do
def _spawn_gather_n(n: int, duration: float):
    tasks = []
    for i in range(n):
        t = yield Spawn(_sleep_task(i, duration), daemon=False)
        tasks.append(t)
    return list((yield Gather(*tasks)))


def test_10_tasks_are_concurrent():
    """10 tasks sleeping 0.5s each. If parallel: ~0.5s. If serial: ~5s."""
    n = 10
    sleep_duration = 0.5

    start = time.monotonic()
    r = run(_spawn_gather_n(n, sleep_duration), handlers=default_handlers())
    elapsed = time.monotonic() - start

    assert r.is_ok(), f"Failed: {r.error}"
    assert len(r.value) == n

    # If truly concurrent, should finish in ~sleep_duration + overhead
    # If serial, would take ~n * sleep_duration = 5s
    max_expected = sleep_duration * 3  # generous: 1.5s
    print(f"\n  {n} tasks x {sleep_duration}s sleep: elapsed={elapsed:.2f}s (max_expected={max_expected:.1f}s)")
    assert elapsed < max_expected, (
        f"Tasks appear serial! elapsed={elapsed:.2f}s, expected <{max_expected:.1f}s "
        f"(serial would be ~{n * sleep_duration:.1f}s)"
    )


def test_50_tasks_are_concurrent():
    """50 tasks sleeping 0.5s. If parallel: ~0.5s. If serial: ~25s."""
    n = 50
    sleep_duration = 0.5

    start = time.monotonic()
    r = run(_spawn_gather_n(n, sleep_duration), handlers=default_handlers())
    elapsed = time.monotonic() - start

    assert r.is_ok(), f"Failed: {r.error}"
    assert len(r.value) == n

    max_expected = sleep_duration * 5  # generous: 2.5s
    print(f"\n  {n} tasks x {sleep_duration}s sleep: elapsed={elapsed:.2f}s (max_expected={max_expected:.1f}s)")
    assert elapsed < max_expected, (
        f"Tasks appear serial! elapsed={elapsed:.2f}s, expected <{max_expected:.1f}s "
        f"(serial would be ~{n * sleep_duration:.1f}s)"
    )


# --- With cache() --- (REMOVED: cache decorator no longer available)

# REMOVED: @cache(lifecycle="persistent")
@do
def _cached_sleep_task(i: int, duration: float):
    result = yield Await(_async_sleep(duration))
    return f"{i}:{result}"


@do
def _spawn_gather_cached_n(n: int, duration: float):
    tasks = []
    for i in range(n):
        t = yield Spawn(_cached_sleep_task(i, duration), daemon=False)
        tasks.append(t)
    return list((yield Gather(*tasks)))


@pytest.mark.skip(reason="uses removed API: sqlite_cache_handler")
def test_10_cached_tasks_are_concurrent():
    """10 cached tasks sleeping 0.5s. Should still be parallel."""
    n = 10
    sleep_duration = 0.5

    start = time.monotonic()
    prog = WithHandler(sqlite_cache_handler(None), _spawn_gather_cached_n(n, sleep_duration))
    r = run(prog, handlers=default_handlers())
    elapsed = time.monotonic() - start

    assert r.is_ok(), f"Failed: {r.error}"
    assert len(r.value) == n

    max_expected = sleep_duration * 3
    print(f"\n  {n} cached tasks x {sleep_duration}s sleep: elapsed={elapsed:.2f}s (max_expected={max_expected:.1f}s)")
    assert elapsed < max_expected, (
        f"Cached tasks appear serial! elapsed={elapsed:.2f}s, expected <{max_expected:.1f}s "
        f"(serial would be ~{n * sleep_duration:.1f}s)"
    )


@pytest.mark.skip(reason="uses removed API: sqlite_cache_handler")
def test_50_cached_tasks_are_concurrent():
    """50 cached tasks sleeping 0.5s. Should still be parallel."""
    n = 50
    sleep_duration = 0.5

    start = time.monotonic()
    prog = WithHandler(sqlite_cache_handler(None), _spawn_gather_cached_n(n, sleep_duration))
    r = run(prog, handlers=default_handlers())
    elapsed = time.monotonic() - start

    assert r.is_ok(), f"Failed: {r.error}"
    assert len(r.value) == n

    max_expected = sleep_duration * 5
    print(f"\n  {n} cached tasks x {sleep_duration}s sleep: elapsed={elapsed:.2f}s (max_expected={max_expected:.1f}s)")
    assert elapsed < max_expected, (
        f"Cached tasks appear serial! elapsed={elapsed:.2f}s, expected <{max_expected:.1f}s "
        f"(serial would be ~{n * sleep_duration:.1f}s)"
    )


# --- With semaphore (like throttled_gather) ---

@do
def _throttled_sleep_task(i: int, duration: float, semaphore):
    yield AcquireSemaphore(semaphore)
    result = yield Await(_async_sleep(duration))
    yield ReleaseSemaphore(semaphore)
    return f"{i}:{result}"


@do
def _spawn_gather_throttled(n: int, duration: float, concurrency: int):
    sem = yield CreateSemaphore(concurrency)
    tasks = []
    for i in range(n):
        t = yield Spawn(_throttled_sleep_task(i, duration, sem), daemon=False)
        tasks.append(t)
    return list((yield Gather(*tasks)))


def test_20_tasks_with_semaphore_concurrency_10():
    """20 tasks, sem=10, sleep 0.5s. Should be ~1s (2 batches), not ~10s."""
    n = 20
    concurrency = 10
    sleep_duration = 0.5

    start = time.monotonic()
    r = run(_spawn_gather_throttled(n, sleep_duration, concurrency), handlers=default_handlers())
    elapsed = time.monotonic() - start

    assert r.is_ok(), f"Failed: {r.error}"
    assert len(r.value) == n

    # 2 batches of 10 x 0.5s = ~1s
    max_expected = sleep_duration * (n / concurrency) * 2  # generous: 2s
    print(f"\n  {n} tasks sem={concurrency} x {sleep_duration}s: elapsed={elapsed:.2f}s (max_expected={max_expected:.1f}s)")
    assert elapsed < max_expected, (
        f"Throttled tasks not concurrent within batch! elapsed={elapsed:.2f}s"
    )


# --- cache + semaphore (matches real pipeline) ---

@do
def _cached_throttled_sleep(i: int, duration: float, semaphore):
    yield AcquireSemaphore(semaphore)
    result = yield _cached_sleep_task(i, duration)
    yield ReleaseSemaphore(semaphore)
    return result


@do
def _spawn_gather_cached_throttled(n: int, duration: float, concurrency: int):
    sem = yield CreateSemaphore(concurrency)
    tasks = []
    for i in range(n):
        t = yield Spawn(_cached_throttled_sleep(i, duration, sem), daemon=False)
        tasks.append(t)
    return list((yield Gather(*tasks)))


@pytest.mark.skip(reason="uses removed API: sqlite_cache_handler")
def test_20_cached_throttled_tasks():
    """20 cached+throttled tasks, sem=10, sleep 0.5s. Real pipeline pattern."""
    n = 20
    concurrency = 10
    sleep_duration = 0.5

    start = time.monotonic()
    prog = WithHandler(
        sqlite_cache_handler(None),
        _spawn_gather_cached_throttled(n, sleep_duration, concurrency),
    )
    r = run(prog, handlers=default_handlers())
    elapsed = time.monotonic() - start

    assert r.is_ok(), f"Failed: {r.error}"
    assert len(r.value) == n

    max_expected = sleep_duration * (n / concurrency) * 3  # generous: 3s
    print(f"\n  {n} cached+throttled sem={concurrency} x {sleep_duration}s: elapsed={elapsed:.2f}s (max_expected={max_expected:.1f}s)")
    assert elapsed < max_expected, (
        f"Cached+throttled tasks serial! elapsed={elapsed:.2f}s"
    )
