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
    EffectBase,
    Gather,
    Pass,
    ReleaseSemaphore,
    Resume,
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
        return run(program, handlers=default_handlers())
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _run_sync_with_custom_timeout(program, timeout_seconds: int):
    old = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout_seconds)
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


# -- Layer 5: WithHandler that ACTUALLY handles effects with Resume --
# Matches proboscis-ema's cache_handler: intercepts custom effects,
# resumes continuation with Resume(k, value), passes the rest.


@do
def _worker_with_lookup(n: int):
    cached = yield _Lookup(n)
    result = yield Await(_fake_api_call(cached))
    return result


class TestLayer5WithHandlerResume:
    def test_handler_resume_sync(self) -> None:
        @do
        def _lookup_handler(effect: Effect, k) -> EffectGenerator:
            if isinstance(effect, _Lookup):
                return (yield Resume(k, effect.key))
            yield Pass()

        programs = [_worker_with_lookup(i) for i in range(TASK_COUNT)]
        body = _throttled(programs, CONCURRENCY)
        program = WithHandler(_lookup_handler, body)
        result = _run_sync_with_timeout(program)
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(TASK_COUNT)]

    def test_handler_resume_with_intercept_sync(self) -> None:
        @do
        def _lookup_handler(effect: Effect, k) -> EffectGenerator:
            if isinstance(effect, _Lookup):
                return (yield Resume(k, effect.key))
            yield Pass()

        @do
        def _interceptor(expr):
            return expr

        programs = [_worker_with_lookup(i) for i in range(TASK_COUNT)]
        body = _throttled(programs, CONCURRENCY)
        program = WithIntercept(_interceptor, WithHandler(_lookup_handler, body))
        result = _run_sync_with_timeout(program)
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(TASK_COUNT)]

    @pytest.mark.asyncio
    async def test_handler_resume_async(self) -> None:
        @do
        def _lookup_handler(effect: Effect, k) -> EffectGenerator:
            if isinstance(effect, _Lookup):
                return (yield Resume(k, effect.key))
            yield Pass()

        programs = [_worker_with_lookup(i) for i in range(TASK_COUNT)]
        body = _throttled(programs, CONCURRENCY)
        program = WithHandler(_lookup_handler, body)
        result = await asyncio.wait_for(
            async_run(program, handlers=default_async_handlers()),
            timeout=TIMEOUT_SECONDS,
        )
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(TASK_COUNT)]

    def test_handler_resume_with_try_and_raise_sync(self) -> None:
        storage: dict[int, int] = {}

        @do
        def _cache_handler(effect: Effect, k) -> EffectGenerator:
            if isinstance(effect, _Lookup):
                if effect.key not in storage:
                    raise KeyError(effect.key)
                return (yield Resume(k, storage[effect.key]))
            if isinstance(effect, _Store):
                storage[effect.key] = effect.value
                return (yield Resume(k, None))
            yield Pass()

        @do
        def _worker_cache_miss_then_store(n: int):
            from doeff import Try

            @do
            def _try_lookup():
                return (yield _Lookup(n))

            cached = yield Try(_try_lookup())
            if cached.is_ok():
                return cached.value
            result = yield Await(_fake_api_call(n))
            yield _Store(n, result)
            return result

        programs = [_worker_cache_miss_then_store(i) for i in range(TASK_COUNT)]
        body = _throttled(programs, CONCURRENCY)
        program = WithHandler(_cache_handler, body)
        result = _run_sync_with_timeout(program)
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(TASK_COUNT)]

    def test_handler_resume_high_concurrency_sync(self) -> None:
        high_task_count = 85
        high_concurrency = 40
        storage: dict[int, int] = {}

        @do
        def _cache_handler(effect: Effect, k) -> EffectGenerator:
            if isinstance(effect, _Lookup):
                if effect.key not in storage:
                    raise KeyError(effect.key)
                return (yield Resume(k, storage[effect.key]))
            if isinstance(effect, _Store):
                storage[effect.key] = effect.value
                return (yield Resume(k, None))
            yield Pass()

        @do
        def _interceptor(expr):
            return expr

        @do
        def _worker_full(n: int):
            from doeff import Try

            yield Tell(f"start {n}")

            @do
            def _try_lookup():
                return (yield _Lookup(n))

            cached = yield Try(_try_lookup())
            if cached.is_ok():
                yield Tell(f"cache hit {n}")
                return cached.value
            yield Tell(f"cache miss {n}")
            result = yield Await(_fake_api_call(n))
            yield _Store(n, result)
            yield Tell(f"done {n}")
            return result

        programs = [_worker_full(i) for i in range(high_task_count)]
        body = _throttled(programs, high_concurrency, with_tell=True)
        program = WithIntercept(_interceptor, WithHandler(_cache_handler, body))
        result = _run_sync_with_timeout(program)
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(high_task_count)]

    def test_real_cache_handler_high_concurrency_sync(self) -> None:
        from doeff.effects.cache import CacheGet, CachePut
        from doeff.handlers.cache_handlers import in_memory_cache_handler

        high_task_count = 85
        high_concurrency = 40

        @do
        def _interceptor(expr):
            return expr

        @do
        def _worker_with_cache(n: int):
            from doeff import Try

            yield Tell(f"start {n}")

            @do
            def _try_get():
                return (yield CacheGet(f"key_{n}"))

            cached = yield Try(_try_get())
            if cached.is_ok():
                yield Tell(f"cache hit {n}")
                return cached.value
            yield Tell(f"cache miss {n}")
            result = yield Await(_fake_api_call(n))
            yield CachePut(f"key_{n}", result)
            yield Tell(f"done {n}")
            return result

        programs = [_worker_with_cache(i) for i in range(high_task_count)]
        body = _throttled(programs, high_concurrency, with_tell=True)
        program = WithIntercept(_interceptor, WithHandler(in_memory_cache_handler(), body))
        result = _run_sync_with_timeout(program)
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(high_task_count)]

    def test_nested_kleisli_cache_high_concurrency_sync(self) -> None:
        """Reproduce real pipeline: worker yields a nested KleisliProgram that
        internally performs CacheGet -> miss -> Await -> CachePut.

        This mirrors proboscis-ema's pattern where:
        - news_event_to_index yields cached_sllm__gpt5_openai(text=..., response_format=...)
        - cached_sllm is a @cache decorated KleisliProgram
        - Inside @cache: Try(CacheGet) -> miss -> Try(original_func()) -> CachePut
        - original_func() yields Await(async_api_call)

        All wrapped in: WithIntercept(interceptor, WithHandler(cache_handler, throttled_gather(...)))
        """
        from doeff.effects.cache import CacheGet, CachePut
        from doeff.handlers.cache_handlers import in_memory_cache_handler

        high_task_count = 85
        high_concurrency = 40

        @do
        def _interceptor(expr):
            return expr

        @do
        def _cached_api_call(n: int):
            """Simulates @cache decorated KleisliProgram (like cached_sllm__gpt5_openai).

            Pattern: Try(CacheGet) -> miss -> Await(api) -> CachePut -> return
            """
            from doeff import Try

            cache_key = f"api_result_{n}"

            @do
            def _try_cache_get():
                return (yield CacheGet(cache_key))

            cached = yield Try(_try_cache_get())
            if cached.is_ok():
                return cached.value

            result = yield Await(_fake_api_call(n))
            yield CachePut(cache_key, result)
            return result

        @do
        def _worker(n: int):
            """Simulates news_event_to_index: yields nested KleisliProgram."""
            yield Tell(f"start {n}")
            result = yield _cached_api_call(n)
            yield Tell(f"done {n}")
            return result

        programs = [_worker(i) for i in range(high_task_count)]
        body = _throttled(programs, high_concurrency, with_tell=True)
        program = WithIntercept(_interceptor, WithHandler(in_memory_cache_handler(), body))
        result = _run_sync_with_timeout(program)
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(high_task_count)]

    def test_nested_kleisli_cache_with_ask_high_concurrency_sync(self) -> None:
        """Same as above but with Ask effect to get the sub-KleisliProgram,
        mirroring the real pattern: llm = yield Ask("structured_news_index_llm")
        then result = yield llm(text=..., response_format=...)
        """
        from doeff import Ask, Local
        from doeff.effects.cache import CacheGet, CachePut
        from doeff.handlers.cache_handlers import in_memory_cache_handler

        high_task_count = 85
        high_concurrency = 40

        @do
        def _interceptor(expr):
            return expr

        @do
        def _cached_compute(n: int):
            """Simulates @cache decorated KleisliProgram."""
            from doeff import Try

            cache_key = f"compute_{n}"

            @do
            def _try_cache_get():
                return (yield CacheGet(cache_key))

            cached = yield Try(_try_cache_get())
            if cached.is_ok():
                return cached.value

            result = yield Await(_fake_api_call(n))
            yield CachePut(cache_key, result)
            return result

        @do
        def _worker(n: int):
            """Worker that asks for a callable and invokes it — like _invoke_indexer_llm."""
            yield Tell(f"start {n}")
            compute_fn = yield Ask("compute_fn")
            result = yield compute_fn(n)
            yield Tell(f"done {n}")
            return result

        programs = [_worker(i) for i in range(high_task_count)]
        body = _throttled(programs, high_concurrency, with_tell=True)
        env = {"compute_fn": _cached_compute}
        program = WithIntercept(
            _interceptor,
            WithHandler(in_memory_cache_handler(), Local(env, body)),
        )
        # Same correctness-oriented stress profile as the sqlite-backed variant above.
        result = _run_sync_with_custom_timeout(program, 30)
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(high_task_count)]

    def test_cache_decorator_nested_kleisli_high_concurrency_sync(self) -> None:
        """Uses the real @cache decorator with sqlite_cache_handler.

        This is the closest reproduction of proboscis-ema's pattern:
        - @cache decorated KleisliProgram (like cached_sllm__gpt5_openai)
        - sqlite_cache_handler via WithHandler
        - WithIntercept with passthrough interceptor
        - Ask effect to get the cached callable
        - Finally + Semaphore for concurrency control
        """
        import tempfile

        from doeff import Ask, Local
        from doeff.cache import cache
        from doeff.handlers.cache_handlers import sqlite_cache_handler

        high_task_count = 85
        high_concurrency = 40

        @do
        def _interceptor(expr):
            return expr

        @cache(lifecycle="persistent")
        @do
        def _cached_compute(n: int):
            result = yield Await(_fake_api_call(n))
            return result

        @do
        def _worker(n: int):
            yield Tell(f"start {n}")
            compute_fn = yield Ask("compute_fn")
            result = yield compute_fn(n=n)
            yield Tell(f"done {n}")
            return result

        programs = [_worker(i) for i in range(high_task_count)]
        body = _throttled(programs, high_concurrency, with_tell=True)
        env = {"compute_fn": _cached_compute}

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/test_cache.sqlite3"
            program = WithIntercept(
                _interceptor,
                WithHandler(sqlite_cache_handler(db_path), Local(env, body)),
            )
            # This is a high-concurrency correctness stress case; the exact wall-clock budget is
            # less important than verifying the scheduler/cache stack makes forward progress.
            result = _run_sync_with_custom_timeout(program, 30)

        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(high_task_count)]

    def test_try_wrapped_await_in_nested_kleisli_high_concurrency_sync(self) -> None:
        """Minimal test: nested KleisliProgram with Try(sub_program) where sub_program yields Await.
        This isolates whether Try wrapping an Await-containing program triggers the bug."""
        from doeff import Ask, Local
        from doeff.effects.cache import CacheGet, CachePut
        from doeff.handlers.cache_handlers import in_memory_cache_handler

        high_task_count = 85
        high_concurrency = 40

        @do
        def _interceptor(expr):
            return expr

        @do
        def _compute(n: int):
            result = yield Await(_fake_api_call(n))
            return result

        @do
        def _cached_compute_with_try(n: int):
            from doeff import Try

            cache_key = f"compute_{n}"

            @do
            def _try_cache_get():
                return (yield CacheGet(cache_key))

            cached = yield Try(_try_cache_get())
            if cached.is_ok():
                return cached.value

            result = yield Try(_compute(n))
            if result.is_ok():
                yield CachePut(cache_key, result.value)
                return result.value
            raise result.error

        @do
        def _worker(n: int):
            yield Tell(f"start {n}")
            compute_fn = yield Ask("compute_fn")
            result = yield compute_fn(n)
            yield Tell(f"done {n}")
            return result

        programs = [_worker(i) for i in range(high_task_count)]
        body = _throttled(programs, high_concurrency, with_tell=True)
        env = {"compute_fn": _cached_compute_with_try}
        program = WithIntercept(
            _interceptor,
            WithHandler(in_memory_cache_handler(), Local(env, body)),
        )
        # Same correctness-oriented stress profile as the sqlite-backed variant above.
        result = _run_sync_with_custom_timeout(program, 30)
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(high_task_count)]

    def test_get_execution_context_in_nested_kleisli_high_concurrency_sync(self) -> None:
        """Test if GetExecutionContext in nested KleisliProgram triggers the bug."""
        from doeff import Ask, Local
        from doeff.effects.cache import CacheGet, CachePut
        from doeff.effects.execution_context import GetExecutionContext
        from doeff.handlers.cache_handlers import in_memory_cache_handler

        high_task_count = 85
        high_concurrency = 40

        @do
        def _interceptor(expr):
            return expr

        @do
        def _compute(n: int):
            result = yield Await(_fake_api_call(n))
            return result

        @do
        def _cached_compute_with_context(n: int):
            from doeff import Try

            _context = yield GetExecutionContext()

            cache_key = f"compute_{n}"

            @do
            def _try_cache_get():
                return (yield CacheGet(cache_key))

            cached = yield Try(_try_cache_get())
            if cached.is_ok():
                return cached.value

            result = yield Try(_compute(n))
            if result.is_ok():
                yield CachePut(cache_key, result.value)
                return result.value
            raise result.error

        @do
        def _worker(n: int):
            yield Tell(f"start {n}")
            compute_fn = yield Ask("compute_fn")
            result = yield compute_fn(n)
            yield Tell(f"done {n}")
            return result

        programs = [_worker(i) for i in range(high_task_count)]
        body = _throttled(programs, high_concurrency, with_tell=True)
        env = {"compute_fn": _cached_compute_with_context}
        program = WithIntercept(
            _interceptor,
            WithHandler(in_memory_cache_handler(), Local(env, body)),
        )
        # This is a correctness stress test for interleaved scheduler tasks, not a strict
        # performance budget. The structural dispatch derivation is slower than the old side
        # table under this load, so allow a wider timeout while still detecting deadlocks.
        result = _run_sync_with_custom_timeout(program, 30)
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(high_task_count)]

    def test_minimal_get_execution_context_semaphore_bug(self) -> None:
        """Minimal reproduction: GetExecutionContext + Await in spawned task with Finally+Semaphore."""
        from doeff.effects.execution_context import GetExecutionContext

        high_task_count = 85
        high_concurrency = 40

        @do
        def _worker(n: int):
            _context = yield GetExecutionContext()
            result = yield Await(_fake_api_call(n))
            return result

        programs = [_worker(i) for i in range(high_task_count)]
        body = _throttled(programs, high_concurrency)
        result = _run_sync_with_timeout(body)
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(high_task_count)]

    def test_cache_decorator_in_memory_high_concurrency_sync(self) -> None:
        """Same as sqlite test but with in_memory_cache_handler to isolate I/O vs @cache logic."""
        from doeff import Ask, Local
        from doeff.cache import cache
        from doeff.handlers.cache_handlers import in_memory_cache_handler

        high_task_count = 85
        high_concurrency = 40

        @do
        def _interceptor(expr):
            return expr

        @cache(lifecycle="persistent")
        @do
        def _cached_compute(n: int):
            result = yield Await(_fake_api_call(n))
            return result

        @do
        def _worker(n: int):
            yield Tell(f"start {n}")
            compute_fn = yield Ask("compute_fn")
            result = yield compute_fn(n=n)
            yield Tell(f"done {n}")
            return result

        programs = [_worker(i) for i in range(high_task_count)]
        body = _throttled(programs, high_concurrency, with_tell=True)
        env = {"compute_fn": _cached_compute}
        program = WithIntercept(
            _interceptor,
            WithHandler(in_memory_cache_handler(), Local(env, body)),
        )
        # Same correctness-oriented stress profile as the sqlite-backed variant above.
        result = _run_sync_with_custom_timeout(program, 30)
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(high_task_count)]

    @pytest.mark.asyncio
    async def test_handler_resume_full_stack_async(self) -> None:
        @do
        def _lookup_handler(effect: Effect, k) -> EffectGenerator:
            if isinstance(effect, _Lookup):
                return (yield Resume(k, effect.key))
            yield Pass()

        @do
        def _interceptor(expr):
            return expr

        programs = [_worker_with_lookup(i) for i in range(TASK_COUNT)]
        body = _throttled(programs, CONCURRENCY, with_tell=True)
        program = WithIntercept(_interceptor, WithHandler(_lookup_handler, body))
        result = await asyncio.wait_for(
            async_run(program, handlers=default_async_handlers()),
            timeout=TIMEOUT_SECONDS,
        )
        assert result.is_ok(), result.display()
        assert result.value == [i * 10 for i in range(TASK_COUNT)]
