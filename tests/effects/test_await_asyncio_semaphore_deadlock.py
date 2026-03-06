"""Await(asyncio.Semaphore) + Spawn/Gather deadlocks under sync run().

sync_await_handler creates ExternalPromise for each Await(coroutine).
When spawned tasks contend on the same asyncio.Semaphore, the scheduler
cannot interleave acquire/release, causing a deadlock.

Native CreateSemaphore/AcquireSemaphore/ReleaseSemaphore are handled
by the Rust VM scheduler directly and work correctly.
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
from doeff.effects import Await

DEADLOCK_TIMEOUT_SECONDS = 5


def _timeout_handler(signum: int, frame: Any) -> None:
    raise TimeoutError(f"Deadlock detected after {DEADLOCK_TIMEOUT_SECONDS}s")


def _run_with_timeout(program):
    old = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(DEADLOCK_TIMEOUT_SECONDS)
    try:
        return run(program, handlers=default_handlers())
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


@do
def _worker(n: int):
    return n * 2
    yield


def _async_gather(programs: list):
    @do
    def _impl():
        tasks = []
        for p in programs:
            task = yield Spawn(p, daemon=False)
            tasks.append(task)
        values = yield Gather(*tasks)
        return list(values)

    return _impl()


def _wrap_with_asyncio_semaphore(program, semaphore: asyncio.Semaphore):
    @do
    def _impl():
        yield Await(semaphore.acquire())
        result = yield program
        semaphore.release()
        return result

    return _impl()


def _throttled_gather_asyncio(programs: list, concurrency: int):
    @do
    def _impl():
        semaphore = asyncio.Semaphore(concurrency)
        wrapped = [_wrap_with_asyncio_semaphore(p, semaphore) for p in programs]
        return (yield _async_gather(wrapped))

    return _impl()


def _wrap_with_native_semaphore(program, sem):
    @do
    def _impl():
        yield AcquireSemaphore(sem)
        result = yield program
        yield ReleaseSemaphore(sem)
        return result

    return _impl()


def _throttled_gather_native(programs: list, concurrency: int):
    @do
    def _impl():
        sem = yield CreateSemaphore(concurrency)
        wrapped = [_wrap_with_native_semaphore(p, sem) for p in programs]
        return (yield _async_gather(wrapped))

    return _impl()


class TestAwaitAsyncioSemaphoreDeadlock:
    def test_native_semaphore_works(self) -> None:
        programs = [_worker(n=i) for i in range(50)]
        p = _throttled_gather_native(programs, concurrency=10)
        result = _run_with_timeout(p)

        assert result.is_ok(), f"Native semaphore should work: {result.display()}"
        assert result.value == [i * 2 for i in range(50)]

    def test_asyncio_semaphore_deadlocks(self) -> None:
        programs = [_worker(n=i) for i in range(50)]
        p = _throttled_gather_asyncio(programs, concurrency=10)
        result = _run_with_timeout(p)

        assert not result.is_ok(), (
            "Expected deadlock with Await(asyncio.Semaphore) + Spawn/Gather under sync run(), "
            "but it succeeded — if this passes, the runtime fixed the interaction"
        )

    @pytest.mark.asyncio
    async def test_asyncio_semaphore_works_in_async_mode(self) -> None:
        programs = [_worker(n=i) for i in range(50)]
        p = _throttled_gather_asyncio(programs, concurrency=10)
        result = await asyncio.wait_for(
            async_run(p, handlers=default_async_handlers()),
            timeout=DEADLOCK_TIMEOUT_SECONDS,
        )

        assert result.is_ok(), (
            "Await(asyncio.Semaphore) + Spawn/Gather should succeed under async_run() "
            "when awaitables stay on the caller event loop"
        )
        assert result.value == [i * 2 for i in range(50)]
