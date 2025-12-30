import threading

import pytest

from doeff import Get, ProgramInterpreter, Put, Thread, do, EffectGenerator
from doeff.cesk import Parallel


@pytest.mark.asyncio
async def test_thread_effect_dedicated_runs_in_new_thread_and_updates_state() -> None:
    engine = ProgramInterpreter()
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
    assert stored_value == worker_name


@pytest.mark.asyncio
async def test_thread_effect_pooled_uses_executor_threads() -> None:
    engine = ProgramInterpreter()

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
        assert name.startswith("doeff-thread-pool")
        assert name != threading.current_thread().name


@pytest.mark.asyncio
async def test_thread_effect_daemon_sets_daemon_flag() -> None:
    engine = ProgramInterpreter()

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
    """Thread with await_result=False returns awaitable.

    NOTE: The old FutureParallelEffect that accepted awaitables has been removed.
    This test now awaits the thread futures individually.
    """
    from doeff import Await
    engine = ProgramInterpreter()

    @do
    def worker(identifier: str) -> EffectGenerator[str]:
        yield Put(identifier, threading.current_thread().name)
        return identifier

    @do
    def program() -> EffectGenerator[list[str]]:
        future_one = yield Thread(worker("first"), strategy="pooled", await_result=False)
        future_two = yield Thread(worker("second"), strategy="dedicated", await_result=False)
        # Await futures individually since CESK Parallel takes Programs, not awaitables
        result_one = yield Await(future_one)
        result_two = yield Await(future_two)
        state_first = yield Get("first")
        state_second = yield Get("second")
        return [result_one, result_two, state_first, state_second]

    result = await engine.run_async(program())
    assert result.is_ok
    values = result.value
    assert sorted(values[:2]) == ["first", "second"]
    assert values[2] != values[3]
