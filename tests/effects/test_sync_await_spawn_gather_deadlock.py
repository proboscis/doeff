"""Reproduce Gather returning wrong value when tasks are wrapped with native semaphore.

Bug: When spawned tasks are wrapped with AcquireSemaphore/ReleaseSemaphore
before being Gathered, Gather returns a single scalar value (one task's result)
instead of a list of all results.

Without semaphore wrapping, Spawn/Gather works correctly with both sync run()
and async_run(). The bug manifests in BOTH execution modes.

Ref: proboscis-ema index_events pipeline — after fixing Bugs 1-3, the pipeline
progresses to indexing but Gather returns a scalar instead of collecting all
results, causing downstream failures.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

import pytest

from doeff import (
    AcquireSemaphore,
    CreateSemaphore,
    Gather,
    ReleaseSemaphore,
    Spawn,
    async_run,
    default_async_handlers,
    default_handlers,
    do,
    run,
)
from doeff import Await

TIMEOUT_SECONDS = 10


def _timeout_handler(signum: int, frame: Any) -> None:
    raise TimeoutError(f"Deadlock detected after {TIMEOUT_SECONDS}s")


def _run_sync_with_timeout(program):
    old = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(TIMEOUT_SECONDS)
    try:
        return run(program, handlers=default_handlers())
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


async def _fake_api_call(n: int) -> int:
    await asyncio.sleep(0.01)
    return n * 10


@do
def _worker_with_await(n: int):
    result = yield Await(_fake_api_call(n))
    return result


def _spawn_gather(programs: list):
    @do
    def _impl():
        tasks = []
        for p in programs:
            task = yield Spawn(p, daemon=False)
            tasks.append(task)
        return list((yield Gather(*tasks)))

    return _impl()


def _wrap_with_semaphore(program, sem):
    @do
    def _impl():
        yield AcquireSemaphore(sem)
        result = yield program
        yield ReleaseSemaphore(sem)
        return result

    return _impl()


def _throttled_spawn_gather(programs: list, concurrency: int):
    @do
    def _impl():
        sem = yield CreateSemaphore(concurrency)
        wrapped = [_wrap_with_semaphore(p, sem) for p in programs]
        return list((yield _spawn_gather(wrapped)))

    return _impl()


class TestSpawnGatherWithoutSemaphore:
    def test_single_spawn_await(self) -> None:
        result = _run_sync_with_timeout(_spawn_gather([_worker_with_await(1)]))
        assert result.is_ok(), result.display()
        assert result.value == [10]

    def test_few_spawned_await_tasks(self) -> None:
        programs = [_worker_with_await(i) for i in range(5)]
        result = _run_sync_with_timeout(_spawn_gather(programs))
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(5)]

    def test_many_spawned_await_tasks(self) -> None:
        programs = [_worker_with_await(i) for i in range(20)]
        result = _run_sync_with_timeout(_spawn_gather(programs))
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(20)]

    @pytest.mark.asyncio
    async def test_many_spawned_await_tasks_async(self) -> None:
        programs = [_worker_with_await(i) for i in range(20)]
        result = await asyncio.wait_for(
            async_run(_spawn_gather(programs), handlers=default_async_handlers()),
            timeout=TIMEOUT_SECONDS,
        )
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(20)]


class TestSpawnGatherWithNativeSemaphore:
    def test_throttled_spawn_gather_sync(self) -> None:
        programs = [_worker_with_await(i) for i in range(20)]
        result = _run_sync_with_timeout(_throttled_spawn_gather(programs, concurrency=5))
        assert result.is_ok(), result.display()
        assert isinstance(result.value, list), (
            f"Gather should return list but got {type(result.value).__name__}: {result.value}"
        )
        assert result.value == [i * 10 for i in range(20)]

    @pytest.mark.asyncio
    async def test_throttled_spawn_gather_async(self) -> None:
        programs = [_worker_with_await(i) for i in range(20)]
        result = await asyncio.wait_for(
            async_run(
                _throttled_spawn_gather(programs, concurrency=5),
                handlers=default_async_handlers(),
            ),
            timeout=TIMEOUT_SECONDS,
        )
        assert result.is_ok(), result.display()
        assert isinstance(result.value, list), (
            f"Gather should return list but got {type(result.value).__name__}: {result.value}"
        )
        assert result.value == [i * 10 for i in range(20)]
