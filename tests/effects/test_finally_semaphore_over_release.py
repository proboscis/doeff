"""Reproduce Bug 6: semaphore released too many times with Finally + Spawn/Gather.

The basic pattern (Finally + Semaphore + Spawn/Gather) works in isolation.
The bug manifests in proboscis-ema's pipeline which has a richer handler stack:
  WithIntercept(loguru_interceptor, ...)
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

import pytest

from doeff import (
    AcquireSemaphore,
    CreateSemaphore,
    Effect,
    Gather,
    Pass,
    ReleaseSemaphore,
    Spawn,
    Tell,
    WithHandler,
    WithIntercept,
    async_run,
    default_async_handlers,
    default_handlers,
    do,
    run,
)
from doeff.effects import Await
from doeff.types import EffectGenerator

TIMEOUT_SECONDS = 10
TASK_COUNT = 20
CONCURRENCY = 5


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
def _worker_simple(n: int):
    result = yield Await(_fake_api_call(n))
    return result


@do
def _worker_with_tell(n: int):
    yield Tell(f"start {n}")
    result = yield Await(_fake_api_call(n))
    yield Tell(f"done {n}")
    return result


def _wrap_with_semaphore_cleanup(program, sem):
    @do
    def _impl():
        yield AcquireSemaphore(sem)
        try:
            result = yield program
            return result
        finally:
            yield ReleaseSemaphore(sem)

    return _impl()


def _wrap_with_semaphore_cleanup_and_tell(program, sem):
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
            task = yield Spawn(p, daemon=False)
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
    def test_finally_semaphore_sync(self) -> None:
        programs = [_worker_simple(i) for i in range(TASK_COUNT)]
        result = _run_sync_with_timeout(_throttled(programs, CONCURRENCY))
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(TASK_COUNT)]

    @pytest.mark.asyncio
    async def test_finally_semaphore_async(self) -> None:
        programs = [_worker_simple(i) for i in range(TASK_COUNT)]
        result = await asyncio.wait_for(
            async_run(_throttled(programs, CONCURRENCY), handlers=default_async_handlers()),
            timeout=TIMEOUT_SECONDS,
        )
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(TASK_COUNT)]


# -- Layer 1: + WithIntercept (noop passthrough) --


class TestLayer1WithIntercept:
    def test_with_noop_intercept_sync(self) -> None:
        @do
        def _interceptor(expr):
            return expr

        programs = [_worker_simple(i) for i in range(TASK_COUNT)]
        program = WithIntercept(_interceptor, _throttled(programs, CONCURRENCY))
        result = _run_sync_with_timeout(program)
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(TASK_COUNT)]

    @pytest.mark.asyncio
    async def test_with_noop_intercept_async(self) -> None:
        @do
        def _interceptor(expr):
            return expr

        programs = [_worker_simple(i) for i in range(TASK_COUNT)]
        program = WithIntercept(_interceptor, _throttled(programs, CONCURRENCY))
        result = await asyncio.wait_for(
            async_run(program, handlers=default_async_handlers()),
            timeout=TIMEOUT_SECONDS,
        )
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(TASK_COUNT)]


# -- Layer 2: + Tell effects inside workers and wrappers --


class TestLayer2WithTell:
    def test_tell_workers_sync(self) -> None:
        programs = [_worker_with_tell(i) for i in range(TASK_COUNT)]
        result = _run_sync_with_timeout(_throttled(programs, CONCURRENCY, with_tell=True))
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(TASK_COUNT)]

    def test_tell_workers_with_intercept_sync(self) -> None:
        @do
        def _interceptor(expr):
            return expr

        programs = [_worker_with_tell(i) for i in range(TASK_COUNT)]
        program = WithIntercept(_interceptor, _throttled(programs, CONCURRENCY, with_tell=True))
        result = _run_sync_with_timeout(program)
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(TASK_COUNT)]


# -- Layer 3: + WithHandler wrapping (simulating cache_handler pattern) --


class TestLayer3WithHandler:
    def test_with_handler_sync(self) -> None:
        @do
        def _passthrough_handler(effect: Effect, k) -> EffectGenerator:
            yield Pass()

        programs = [_worker_simple(i) for i in range(TASK_COUNT)]
        body = _throttled(programs, CONCURRENCY)
        program = WithHandler(_passthrough_handler, body)
        result = _run_sync_with_timeout(program)
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(TASK_COUNT)]

    def test_with_handler_and_intercept_sync(self) -> None:
        @do
        def _passthrough_handler(effect: Effect, k) -> EffectGenerator:
            yield Pass()

        @do
        def _interceptor(expr):
            return expr

        programs = [_worker_simple(i) for i in range(TASK_COUNT)]
        body = _throttled(programs, CONCURRENCY)
        program = WithIntercept(_interceptor, WithHandler(_passthrough_handler, body))
        result = _run_sync_with_timeout(program)
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(TASK_COUNT)]


# -- Layer 4: Full stack (intercept + handler + tell) --


class TestLayer4FullStack:
    def test_full_stack_sync(self) -> None:
        @do
        def _passthrough_handler(effect: Effect, k) -> EffectGenerator:
            yield Pass()

        @do
        def _interceptor(expr):
            return expr

        programs = [_worker_with_tell(i) for i in range(TASK_COUNT)]
        body = _throttled(programs, CONCURRENCY, with_tell=True)
        program = WithIntercept(_interceptor, WithHandler(_passthrough_handler, body))
        result = _run_sync_with_timeout(program)
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(TASK_COUNT)]

    @pytest.mark.asyncio
    async def test_full_stack_async(self) -> None:
        @do
        def _passthrough_handler(effect: Effect, k) -> EffectGenerator:
            yield Pass()

        @do
        def _interceptor(expr):
            return expr

        programs = [_worker_with_tell(i) for i in range(TASK_COUNT)]
        body = _throttled(programs, CONCURRENCY, with_tell=True)
        program = WithIntercept(_interceptor, WithHandler(_passthrough_handler, body))
        result = await asyncio.wait_for(
            async_run(program, handlers=default_async_handlers()),
            timeout=TIMEOUT_SECONDS,
        )
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(TASK_COUNT)]
