"""Reproduce Bug 6: semaphore released too many times with Finally + Spawn/Gather.

The basic pattern (Finally + Semaphore + Spawn/Gather) works in isolation.
The bug manifests in proboscis-ema's pipeline which has a richer handler stack:
  WithObserve(loguru_observer, ...)
    WithHandler(cache_handler, ...)
      WithHandler(sync_await_handler, ...)
        spawn_intercept_handler
          SchedulerHandler

This test systematically adds layers to isolate which combination triggers the
over-release.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

from doeff import (
    AcquireSemaphore,
    Await,
    CreateSemaphore,
    EffectBase,
    Gather,
    ReleaseSemaphore,
    Spawn,
    Tell,
    WithObserve,
    do,
)
from tests._run_helpers import run_with_defaults


class _Lookup(EffectBase):
    """Custom effect handled by a handler with Resume (simulates CacheGet)."""

    def __init__(self, key: int) -> None:
        super().__init__()
        self.key = key


class _Store(EffectBase):
    """Custom effect for storing a value (simulates CachePut)."""

    def __init__(self, key: int, value: Any) -> None:
        super().__init__()
        self.key = key
        self.value = value


TIMEOUT_SECONDS = 10
TASK_COUNT = 20
CONCURRENCY = 5


def _timeout_handler(signum: int, frame: Any) -> None:
    raise TimeoutError(f"Deadlock detected after {TIMEOUT_SECONDS}s")


def _run_sync_with_timeout(program):
    old = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(TIMEOUT_SECONDS)
    try:
        return run_with_defaults(program)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _run_sync_with_custom_timeout(program, timeout_seconds: int):
    old = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout_seconds)
    try:
        return run_with_defaults(program)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


async def _fake_api_call(n: int) -> int:
    await asyncio.sleep(0.01)
    return n * 10


@do
def _worker_simple(n: int):
    result = yield Await(_fake_api_call(n))
    return result


@do
def _worker_with_tell(n: int):
    yield Tell(f"start {n}")
    result = yield Await(_fake_api_call(n))
    yield Tell(f"done {n}")
    return result


def _wrap_with_semaphore_cleanup(program: Any, sem):
    @do
    def _impl():
        yield AcquireSemaphore(sem)
        try:
            result = yield program
            return result
        finally:
            yield ReleaseSemaphore(sem)

    return _impl()


def _wrap_with_semaphore_cleanup_and_tell(program: Any, sem):
    @do
    def _impl():
        yield Tell("acquiring semaphore")
        yield AcquireSemaphore(sem)
        try:
            yield Tell("semaphore acquired")
            result = yield program
            return result
        finally:
            yield ReleaseSemaphore(sem)

    return _impl()


def _spawn_gather(programs: list):
    @do
    def _impl():
        tasks = []
        for p in programs:
            task = yield Spawn(p)
            tasks.append(task)
        return list((yield Gather(*tasks)))

    return _impl()


def _throttled(programs: list, concurrency: int, with_tell: bool = False):
    @do
    def _impl():
        sem = yield CreateSemaphore(concurrency)
        wrap = _wrap_with_semaphore_cleanup_and_tell if with_tell else _wrap_with_semaphore_cleanup
        wrapped = [wrap(p, sem) for p in programs]
        return list((yield _spawn_gather(wrapped)))

    return _impl()


# -- Layer 0: Baseline (no extra handlers) --


class TestLayer0Baseline:
    pass


# -- Layer 1: + WithIntercept (noop passthrough) --


class TestLayer1WithIntercept:
    pass


# -- Layer 2: + Tell effects inside workers and wrappers --


class TestLayer2WithTell:
    def test_tell_workers_sync(self) -> None:
        programs = [_worker_with_tell(i) for i in range(TASK_COUNT)]
        result = _run_sync_with_timeout(_throttled(programs, CONCURRENCY, with_tell=True))
        assert result.is_ok(), result.error
        assert result.value == [i * 10 for i in range(TASK_COUNT)]

    def test_tell_workers_with_observe_sync(self) -> None:
        programs = [_worker_with_tell(i) for i in range(TASK_COUNT)]
        program = WithObserve(lambda _effect: None, _throttled(programs, CONCURRENCY, with_tell=True))
        result = _run_sync_with_timeout(program)
        assert result.is_ok(), result.error
        assert result.value == [i * 10 for i in range(TASK_COUNT)]


# -- Layer 3: + WithHandler wrapping (simulating cache_handler pattern) --


class TestLayer3WithHandler:
    pass


# -- Layer 4: Full stack (intercept + handler + tell) --


class TestLayer4FullStack:
    pass


# -- Layer 5: WithHandler that ACTUALLY handles effects with Resume --
# Matches proboscis-ema's cache_handler: intercepts custom effects,
# resumes continuation with Resume(k, value), passes the rest.


@do
def _worker_with_lookup(n: int):
    cached = yield _Lookup(n)
    result = yield Await(_fake_api_call(cached))
    return result


class TestLayer5WithHandlerResume:
    pass
