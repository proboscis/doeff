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
from pathlib import Path
import signal
from typing import Any

import pytest

from doeff import (
    AcquireSemaphore,
    CreateSemaphore,
    Effect,
    Finally,
    Gather,
    Pass,
    ReleaseSemaphore,
    Spawn,
    Tell,
    WithHandler,
    WithIntercept,
    async_run,
    cache,
    default_async_handlers,
    default_handlers,
    do,
    run,
)
from doeff.effects import Await, GetExecutionContext
from doeff.handlers import in_memory_cache_handler, sqlite_cache_handler
from doeff.types import EffectGenerator

TIMEOUT_SECONDS = 10
TASK_COUNT = 20
CONCURRENCY = 5
LAYER5_TASK_COUNT = 85
LAYER5_CONCURRENCY = 40


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


@do
def _worker_with_execution_context(n: int):
    _context = yield GetExecutionContext()
    result = yield Await(_fake_api_call(n))
    return result


@do
def _leaf_with_execution_context(n: int):
    _context = yield GetExecutionContext()
    result = yield Await(_fake_api_call(n))
    return result


@do
def _nested_worker_with_execution_context(n: int):
    first = yield _leaf_with_execution_context(n)
    return first + n


def _wrap_with_semaphore_finally(program, sem):
    @do
    def _impl():
        yield AcquireSemaphore(sem)
        yield Finally(ReleaseSemaphore(sem))
        result = yield program
        return result

    return _impl()


def _wrap_with_semaphore_finally_and_tell(program, sem):
    @do
    def _impl():
        yield Tell("acquiring semaphore")
        yield AcquireSemaphore(sem)
        yield Finally(ReleaseSemaphore(sem))
        yield Tell("semaphore acquired")
        result = yield program
        return result

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
        wrap = _wrap_with_semaphore_finally_and_tell if with_tell else _wrap_with_semaphore_finally
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


# -- Layer 5: + Transfer from GetExecutionContext inside throttled spawned tasks --


class TestLayer5WithHandlerResume:
    def test_minimal_get_execution_context_semaphore_bug(self) -> None:
        programs = [_worker_with_execution_context(i) for i in range(LAYER5_TASK_COUNT)]
        result = _run_sync_with_timeout(_throttled(programs, LAYER5_CONCURRENCY))
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(LAYER5_TASK_COUNT)]

    def test_cache_decorator_in_memory_high_concurrency_sync(self) -> None:
        calls = {"count": 0}

        @cache()
        @do
        def _cached_worker(n: int):
            calls["count"] += 1
            return (yield Await(_fake_api_call(n)))

        programs = [_cached_worker(i) for i in range(LAYER5_TASK_COUNT)]
        program = WithHandler(in_memory_cache_handler(), _throttled(programs, LAYER5_CONCURRENCY))
        result = _run_sync_with_timeout(program)
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(LAYER5_TASK_COUNT)]
        assert calls["count"] == LAYER5_TASK_COUNT

    def test_cache_decorator_nested_kleisli_high_concurrency_sync(self, tmp_path: Path) -> None:
        calls = {"count": 0}

        @cache()
        @do
        def _cached_leaf(n: int):
            calls["count"] += 1
            return (yield Await(_fake_api_call(n)))

        @do
        def _nested_worker(n: int):
            first = yield _cached_leaf(n)
            second = yield _cached_leaf(n)
            return first + second

        programs = [_nested_worker(i) for i in range(LAYER5_TASK_COUNT)]
        db_path = tmp_path / "layer5_cache.sqlite3"
        program = WithHandler(sqlite_cache_handler(db_path), _throttled(programs, LAYER5_CONCURRENCY))
        result = _run_sync_with_timeout(program)
        assert result.is_ok(), result.display()
        assert result.value == [i * 20 for i in range(LAYER5_TASK_COUNT)]
        assert calls["count"] == LAYER5_TASK_COUNT
        assert db_path.exists()

    def test_get_execution_context_in_nested_kleisli_high_concurrency_sync(self) -> None:
        programs = [_nested_worker_with_execution_context(i) for i in range(LAYER5_TASK_COUNT)]
        result = _run_sync_with_timeout(_throttled(programs, LAYER5_CONCURRENCY))
        assert result.is_ok(), result.display()
        assert result.value == [i * 11 for i in range(LAYER5_TASK_COUNT)]
