"""Reproducer: cache() + Await + Spawn+Gather hangs at scale.

When many Spawned tasks enter cache compute_and_cache() simultaneously and each
task does a `yield Await(asyncio.sleep(...))` inside the cached function, the Await
never completes and the program hangs indefinitely.

Conditions:
  - cache(lifecycle="persistent") wrapping a @do function that yields Await(...)
  - Spawn+Gather dispatching N of those cached programs concurrently
  - N=100 works fine, N=500 hangs (3 tasks enter compute, 0 complete)
  - Without cache(), the same Await+Spawn pattern works at any N

See: https://github.com/proboscis/doeff/issues/XXX
"""

import asyncio

import pytest

from doeff import (
    Await,
    EffectGenerator,
    Gather,
    Program,
    Spawn,
    WithHandler,
    cache,
    default_handlers,
    do,
    run,
    slog,
)
from doeff.handlers import sqlite_cache_handler


@cache(lifecycle="persistent")
@do
def _cached_async_task(i: int) -> EffectGenerator[str]:
    """A minimal cached task that does an async Await."""
    yield slog(msg=f"[{i}] compute start", level="info")
    yield Await(asyncio.sleep(0.01))
    yield slog(msg=f"[{i}] compute done", level="info")
    return f"result-{i}"


@do
def _no_cache_async_task(i: int) -> EffectGenerator[str]:
    """Same task but without cache — control group."""
    yield slog(msg=f"[{i}] compute start", level="info")
    yield Await(asyncio.sleep(0.01))
    yield slog(msg=f"[{i}] compute done", level="info")
    return f"result-{i}"


@do
def _spawn_gather_n(task_factory, n: int) -> EffectGenerator[list]:
    tasks = []
    for i in range(n):
        t = yield Spawn(task_factory(i), daemon=False)
        tasks.append(t)
    return list((yield Gather(*tasks)))


def _run_with_cache(program: Program):
    wrapped = WithHandler(sqlite_cache_handler(None), program)
    return run(wrapped, handlers=default_handlers())


class TestCacheAwaitSpawnHang:
    """Reproducer for cache + Await + Spawn hang."""

    @pytest.mark.timeout(60)
    def test_no_cache_100_works(self):
        """Control: without cache(), 100 Spawn+Gather+Await works fine."""
        r = run(
            _spawn_gather_n(_no_cache_async_task, 100),
            handlers=default_handlers(),
        )
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == 100

    @pytest.mark.timeout(60)
    def test_no_cache_500_works(self):
        """Control: without cache(), 500 Spawn+Gather+Await works fine."""
        r = run(
            _spawn_gather_n(_no_cache_async_task, 500),
            handlers=default_handlers(),
        )
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == 500

    @pytest.mark.timeout(60)
    def test_cached_50_works(self):
        """cache + Await + 50 tasks works."""
        r = _run_with_cache(_spawn_gather_n(_cached_async_task, 50))
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == 50

    @pytest.mark.timeout(60)
    def test_cached_100_works(self):
        """cache + Await + 100 tasks works."""
        r = _run_with_cache(_spawn_gather_n(_cached_async_task, 100))
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == 100

    @pytest.mark.timeout(60)
    def test_cached_200_hangs(self):
        """BUG: cache + Await + 200 tasks — does it hang?"""
        r = _run_with_cache(_spawn_gather_n(_cached_async_task, 200))
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == 200

    @pytest.mark.timeout(60)
    def test_cached_500_hangs(self):
        """BUG: cache + Await + 500 tasks hangs.

        This test will timeout if the bug is present.
        Tasks enter compute_and_cache but Await(asyncio.sleep) never returns.
        """
        r = _run_with_cache(_spawn_gather_n(_cached_async_task, 500))
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == 500
