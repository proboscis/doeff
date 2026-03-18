"""Reproducer: cache() + Await + Spawn+Gather hangs at scale.

When many Spawned tasks enter cache compute_and_cache() simultaneously and each
task does a `yield Await(coroutine)` inside the cached function, the scheduler
stops dispatching new tasks after the first batch completes.

See: https://github.com/proboscis/doeff/issues/342
"""

from __future__ import annotations

import asyncio
import signal
from dataclasses import dataclass
from typing import Any

import doeff_vm
import pytest

from doeff import (
    Effect,
    EffectBase,
    Gather,
    Pass,
    Resume,
    Spawn,
    WithHandler,
    cache,
    default_handlers,
    do,
    run,
    slog,
)
from doeff.effects import Await
from doeff.handlers import sqlite_cache_handler

TIMEOUT_SECONDS = 30


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


def _run_cached_with_timeout(program):
    wrapped = WithHandler(sqlite_cache_handler(None), program)
    return _run_with_timeout(wrapped)


async def _fake_work(i: int) -> str:
    await asyncio.sleep(0.01)
    return f"done-{i}"


@do
def _no_cache_task(i: int):
    result = yield Await(_fake_work(i))
    return result


@cache(lifecycle="persistent")
@do
def _cached_task(i: int):
    result = yield Await(_fake_work(i))
    return result


@do
def _spawn_gather_n(factory, n: int):
    tasks = []
    for i in range(n):
        t = yield Spawn(factory(i), daemon=False)
        tasks.append(t)
    return list((yield Gather(*tasks)))


@dataclass(frozen=True)
class _ProbeHandlerStack(EffectBase):
    label: str


@do
def _probe_handler(effect: Effect, k):
    if not isinstance(effect, _ProbeHandlerStack):
        yield Pass()
        return
    handlers = yield doeff_vm.GetHandlers()
    stack = [
        None
        if handler is None
        else (
            getattr(handler, "__qualname__", None)
            or getattr(handler, "__name__", None)
            or type(handler).__name__
        )
        for handler in handlers
    ]
    return (yield Resume(k, stack))


@cache(lifecycle="persistent")
@do
def _cached_stack_probe(label: str):
    return (yield _ProbeHandlerStack(label))


class TestCacheAwaitSpawnHang:

    def test_no_cache_100(self):
        r = _run_with_timeout(_spawn_gather_n(_no_cache_task, 100))
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == 100

    def test_no_cache_500(self):
        r = _run_with_timeout(_spawn_gather_n(_no_cache_task, 500))
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == 500

    def test_cached_50(self):
        r = _run_cached_with_timeout(_spawn_gather_n(_cached_task, 50))
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == 50

    def test_cached_100(self):
        r = _run_cached_with_timeout(_spawn_gather_n(_cached_task, 100))
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == 100

    def test_cached_200(self):
        """Hangs without fix — first batch completes but scheduler stops dispatching."""
        r = _run_cached_with_timeout(_spawn_gather_n(_cached_task, 200))
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == 200

    def test_cached_500(self):
        """Hangs without fix — same as 200 but larger."""
        r = _run_cached_with_timeout(_spawn_gather_n(_cached_task, 500))
        assert r.is_ok(), f"Failed: {r.error}"
        assert len(r.value) == 500

    def test_cached_spawn_preserves_handler_stack(self, tmp_path):
        @do
        def program():
            direct = yield _cached_stack_probe("direct")
            task = yield Spawn(_cached_stack_probe("spawned"), daemon=False)
            spawned = (yield Gather(task))[0]
            return direct, spawned

        wrapped = WithHandler(sqlite_cache_handler(tmp_path / "cache.sqlite3"), program())
        result = run(wrapped, handlers=[*default_handlers(), _probe_handler])

        assert result.is_ok(), f"Failed: {result.error}"
        direct, spawned = result.value
        assert direct
        assert direct == spawned
