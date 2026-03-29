from __future__ import annotations

import asyncio
from typing import Any

import pytest

from doeff import (
    Await,
    Gather,
    Spawn,
    Try,
    Wait,
    async_run,
    default_async_handlers,
    default_handlers,
    do,
    race,
    run,
)
from doeff_core_effects.scheduler import TaskCancelledError
# REMOVED: from doeff.traceback import attach_doeff_traceback


def _result_is_ok(result: Any) -> bool:
    probe = getattr(result, "is_ok", None)
    return bool(probe() if callable(probe) else probe)


def _result_is_err(result: Any) -> bool:
    probe = getattr(result, "is_err", None)
    return bool(probe() if callable(probe) else probe)


def test_spawn_gather_basic() -> None:
    @do
    def child(value: int):
        return value

    @do
    def program():
        t1 = yield Spawn(child(1))
        t2 = yield Spawn(child(2))
        values = yield Gather(t1, t2)
        return tuple(values)

    result = run(program(), handlers=default_handlers())
    assert _result_is_ok(result)
    assert result.value == (1, 2)


def test_spawn_wait_basic() -> None:
    @do
    def child():
        return "done"

    @do
    def program():
        task = yield Spawn(child())
        return (yield Wait(task))

    result = run(program(), handlers=default_handlers())
    assert _result_is_ok(result)
    assert result.value == "done"


@pytest.mark.stress
@pytest.mark.slow
@pytest.mark.asyncio
async def test_many_task_switches_no_crash() -> None:
    task_count = 64
    yields_per_task = 8

    @do
    def worker(value: int):
        for _ in range(yields_per_task):
            _ = yield Await(asyncio.sleep(0))
        return value

    @do
    def program():
        tasks = []
        for i in range(task_count):
            tasks.append((yield Spawn(worker(i))))
        _ = yield Gather(*tasks)
        return "completed"

    result = await async_run(program(), handlers=default_async_handlers())
    assert _result_is_ok(result)
    assert result.value == "completed"


@pytest.mark.stress
@pytest.mark.slow
@pytest.mark.asyncio
async def test_many_concurrent_tasks_with_error_propagation() -> None:
    """Stress test: many tasks doing many yields, one crashes mid-execution.
    Verifies Transfer-based task switching doesn't corrupt error propagation
    even under heavy concurrency (10 healthy × 20 yields + 1 crasher × 20 yields
    = 220 total task switches).
    """

    @do
    def healthy_worker(label: str):
        for _ in range(20):
            _ = yield Await(asyncio.sleep(0))
        return label

    @do
    def crasher():
        for _ in range(20):
            _ = yield Await(asyncio.sleep(0))
        raise RuntimeError("boom")

    @do
    def program():
        tasks = []
        for i in range(10):
            tasks.append((yield Spawn(healthy_worker(f"w{i}"))))
        tasks.append((yield Spawn(crasher())))
        return (yield Gather(*tasks))

    result = await async_run(
        program(),
        handlers=default_async_handlers(),
        print_doeff_trace=False,
    )
    assert _result_is_err(result)
    assert isinstance(result.error, RuntimeError)
    assert "boom" in str(result.error)


@pytest.mark.asyncio
async def test_task_error_propagation() -> None:
    @do
    def ok():
        _ = yield Await(asyncio.sleep(0))
        return "ok"

    @do
    def boom():
        _ = yield Await(asyncio.sleep(0))
        raise RuntimeError("task boom")

    @do
    def program():
        t1 = yield Spawn(ok())
        t2 = yield Spawn(boom())
        return (yield Gather(t1, t2))

    result = await async_run(program(), handlers=default_async_handlers())
    assert _result_is_err(result)
    assert isinstance(result.error, RuntimeError)
    assert "task boom" in str(result.error)


@pytest.mark.skip(reason="uses removed API: attach_doeff_traceback")
@pytest.mark.asyncio
async def test_gather_fail_fast_cancels_siblings_and_preserves_traceback() -> None:
    events: list[tuple[str, str]] = []

    @do
    def slow_worker(label: str):
        events.append((label, "start"))
        try:
            _ = yield Await(asyncio.sleep(0.1))
            events.append((label, "done"))
            return label
        except TaskCancelledError:
            events.append((label, "cancelled"))
            raise
        finally:
            events.append((label, "cleanup"))

    @do
    def boom():
        events.append(("boom", "start"))
        _ = yield Await(asyncio.sleep(0))
        raise RuntimeError("gather boom")

    @do
    def program():
        t1 = yield Spawn(slow_worker("left"))
        t2 = yield Spawn(boom())
        t3 = yield Spawn(slow_worker("right"))
        return (yield Gather(t1, t2, t3))

    result = await async_run(
        program(),
        handlers=default_async_handlers(),
        print_doeff_trace=False,
    )

    assert _result_is_err(result)
    assert isinstance(result.error, RuntimeError)
    assert "gather boom" in str(result.error)
    assert ("left", "cancelled") in events
    assert ("right", "cancelled") in events
    assert ("left", "cleanup") in events
    assert ("right", "cleanup") in events
    assert ("left", "done") not in events
    assert ("right", "done") not in events

    assert result.traceback_data is not None
    active_chain = result.traceback_data.active_chain
    assert any(
        entry["kind"] == "effect_yield"
        and entry["function_name"] == "program"
        and "Gather(" in entry["effect_repr"]
        for entry in active_chain
    )
    assert any(
        entry["kind"] == "exception_site"
        and entry["function_name"] == "boom"
        and entry["message"] == "gather boom"
        for entry in active_chain
    )
    rendered = attach_doeff_traceback(
        result.error,
        traceback_data=result.traceback_data,
    ).format_default()
    assert "program()" in rendered
    assert "boom()" in rendered
    assert "yield Gather(" in rendered
    assert "raise RuntimeError('gather boom')" in rendered
    assert not hasattr(result.error, "__doeff_traceback_data__")
    assert not hasattr(result.error, "__doeff_traceback__")


@pytest.mark.asyncio
async def test_try_wraps_gather_fail_fast_error_and_still_cancels_siblings() -> None:
    events: list[tuple[str, str]] = []

    @do
    def slow_worker():
        events.append(("slow", "start"))
        try:
            _ = yield Await(asyncio.sleep(0.1))
            events.append(("slow", "done"))
            return "slow"
        except TaskCancelledError:
            events.append(("slow", "cancelled"))
            raise
        finally:
            events.append(("slow", "cleanup"))

    @do
    def boom():
        events.append(("boom", "start"))
        _ = yield Await(asyncio.sleep(0))
        raise RuntimeError("gather try boom")

    @do
    def program():
        slow_task = yield Spawn(slow_worker())
        boom_task = yield Spawn(boom())
        return (yield Try(Gather(slow_task, boom_task)))

    result = await async_run(program(), handlers=default_async_handlers())

    assert _result_is_ok(result)
    safe_result = result.value
    assert _result_is_err(safe_result)
    assert isinstance(safe_result.error, RuntimeError)
    assert str(safe_result.error) == "gather try boom"
    assert ("slow", "cancelled") in events
    assert ("slow", "cleanup") in events
    assert ("slow", "done") not in events
    context = getattr(safe_result.error, "doeff_execution_context", None)
    assert context is not None
    active_chain = getattr(context, "active_chain", ())
    assert any(
        entry["kind"] == "exception_site"
        and entry["function_name"] == "boom"
        and entry["message"] == "gather try boom"
        for entry in active_chain
    )


@pytest.mark.asyncio
async def test_gather_collects_try_wrapped_children_without_fail_fast_cancellation() -> None:
    events: list[str] = []

    @do
    def fail():
        events.append("fail:start")
        _ = yield Await(asyncio.sleep(0))
        raise RuntimeError("inner fail")

    @do
    def ok():
        events.append("ok:start")
        _ = yield Await(asyncio.sleep(0.01))
        events.append("ok:done")
        return "ok"

    @do
    def program():
        failed_task = yield Spawn(Try(fail()))
        ok_task = yield Spawn(Try(ok()))
        return (yield Gather(failed_task, ok_task))

    result = await async_run(program(), handlers=default_async_handlers())

    assert _result_is_ok(result)
    failed_result, ok_result = result.value
    assert _result_is_err(failed_result)
    assert isinstance(failed_result.error, RuntimeError)
    assert str(failed_result.error) == "inner fail"
    assert _result_is_ok(ok_result)
    assert ok_result.value == "ok"
    assert "ok:done" in events


@pytest.mark.asyncio
async def test_race_with_transfer() -> None:
    @do
    def fast():
        return "fast"

    @do
    def slow():
        _ = yield Await(asyncio.sleep(0.02))
        return "slow"

    @do
    def program():
        fast_task = yield Spawn(fast())
        slow_task = yield Spawn(slow())
        return (yield race(fast_task, slow_task))

    result = await async_run(program(), handlers=default_async_handlers())
    assert _result_is_ok(result)
    assert result.value == "fast"
