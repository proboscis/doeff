"""
CESK interpreter tests for Thread effect.

Adapted from test_thread_effect.py - tests thread execution strategies.
"""

import threading

import pytest

from doeff import EffectGenerator, Get, Parallel, Put, Thread, do
from doeff.cesk_adapter import CESKInterpreter


@pytest.mark.asyncio
async def test_thread_effect_dedicated_runs_in_new_thread_and_updates_state() -> None:
    """Thread with dedicated strategy runs in a new thread and updates state."""
    engine = CESKInterpreter()
    outer_thread = threading.current_thread().name

    @do
    def inner() -> EffectGenerator[tuple[str, bool]]:
        name = threading.current_thread().name
        daemon_flag = threading.current_thread().daemon
        yield Put("worker", name)
        return name, daemon_flag

    @do
    def program() -> EffectGenerator[tuple[str, bool, str | None]]:
        yield Put("worker", outer_thread)
        worker_name, daemon_flag = yield Thread(inner(), strategy="dedicated")
        stored = yield Get("worker")
        return worker_name, daemon_flag, stored

    result = await engine.run_async(program())
    assert result.is_ok
    worker_name, daemon_flag, stored_value = result.value
    assert worker_name != outer_thread
    assert daemon_flag is False
    # With CESK state merging, the child's Put should merge back
    assert stored_value == worker_name


@pytest.mark.asyncio
async def test_thread_effect_pooled_uses_executor_threads() -> None:
    """Thread with pooled strategy uses shared executor threads."""
    engine = CESKInterpreter()

    @do
    def worker() -> EffectGenerator[str]:
        return threading.current_thread().name

    @do
    def program() -> EffectGenerator[list[str]]:
        first = yield Thread(worker(), strategy="pooled")
        second = yield Thread(worker(), strategy="pooled")
        return [first, second]

    result = await engine.run_async(program())
    assert result.is_ok
    worker_threads = result.value
    assert len(worker_threads) == 2
    for name in worker_threads:
        # CESK uses "cesk-pool" prefix instead of "doeff-thread-pool"
        assert "pool" in name.lower() or "cesk" in name.lower()
        assert name != threading.current_thread().name


@pytest.mark.asyncio
async def test_thread_effect_daemon_sets_daemon_flag() -> None:
    """Thread with daemon strategy sets daemon flag on thread."""
    engine = CESKInterpreter()

    @do
    def worker() -> EffectGenerator[bool]:
        return threading.current_thread().daemon

    @do
    def program() -> EffectGenerator[bool]:
        return (yield Thread(worker(), strategy="daemon"))

    result = await engine.run_async(program())
    assert result.is_ok
    assert result.value is True


@pytest.mark.asyncio
async def test_thread_effect_can_return_awaitable_for_parallelism() -> None:
    """Thread with await_result=False returns awaitable for parallel execution.

    NOTE: Unlike ProgramInterpreter which uses mutable context and can merge
    state when the awaitable is eventually awaited, CESK uses immutable stores.
    State from async threads (await_result=False) is NOT merged back.
    Only the return values are collected by Parallel.
    """
    engine = CESKInterpreter()

    @do
    def worker(identifier: str) -> EffectGenerator[str]:
        # NOTE: This Put won't be visible to parent with await_result=False
        yield Put(identifier, threading.current_thread().name)
        return identifier

    @do
    def program() -> EffectGenerator[list[str]]:
        future_one = yield Thread(worker("first"), strategy="pooled", await_result=False)
        future_two = yield Thread(worker("second"), strategy="dedicated", await_result=False)
        results = yield Parallel(future_one, future_two)
        return list(results)

    result = await engine.run_async(program())
    assert result.is_ok
    values = result.value
    # Both worker identifiers should be returned
    assert sorted(values) == ["first", "second"]


@pytest.mark.asyncio
async def test_thread_effect_state_isolation() -> None:
    """Thread sees state at spawn time, not later modifications."""
    engine = CESKInterpreter()

    @do
    def worker() -> EffectGenerator[str | None]:
        return (yield Get("flag"))

    @do
    def program() -> EffectGenerator[tuple[str | None, str | None]]:
        yield Put("flag", "before")
        # Thread captures state at spawn time
        seen = yield Thread(worker(), strategy="dedicated")
        yield Put("flag", "after")
        current = yield Get("flag")
        return seen, current

    result = await engine.run_async(program())
    assert result.is_ok
    # Thread sees "before" (snapshot at spawn), parent sees "after"
    assert result.value == ("before", "after")


@pytest.mark.asyncio
async def test_thread_effect_multiple_threads_sequential() -> None:
    """Multiple threads executed sequentially accumulate results."""
    engine = CESKInterpreter()

    @do
    def worker(n: int) -> EffectGenerator[int]:
        yield Put(f"computed_{n}", n * n)
        return n * n

    @do
    def program() -> EffectGenerator[tuple[list[int], list[int | None]]]:
        results = []
        for i in range(3):
            results.append((yield Thread(worker(i), strategy="pooled")))

        states = []
        for i in range(3):
            states.append((yield Get(f"computed_{i}")))

        return results, states

    result = await engine.run_async(program())
    assert result.is_ok
    # All computations should succeed
    assert result.value[0] == [0, 1, 4]
    # State from threads should be merged
    assert result.value[1] == [0, 1, 4]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
