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
    do,
    race,
)
from doeff_core_effects.scheduler import TaskCancelledError
from tests._run_helpers import run_with_defaults
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

    result = run_with_defaults(program())
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

    result = run_with_defaults(program())
    assert _result_is_ok(result)
    assert result.value == "done"


@pytest.mark.stress
@pytest.mark.slow
def test_many_task_switches_no_crash() -> None:
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

    result = run_with_defaults(program())
    assert _result_is_ok(result)
    assert result.value == "completed"


@pytest.mark.stress
@pytest.mark.slow
def test_many_concurrent_tasks_with_error_propagation() -> None:
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

    result = run_with_defaults(program())
    assert _result_is_err(result)
    assert isinstance(result.error, RuntimeError)
    assert "boom" in str(result.error)


def test_task_error_propagation() -> None:
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

    result = run_with_defaults(program())
    assert _result_is_err(result)
    assert isinstance(result.error, RuntimeError)
    assert "task boom" in str(result.error)




def test_gather_collects_try_wrapped_children_without_fail_fast_cancellation() -> None:
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

    result = run_with_defaults(program())

    assert _result_is_ok(result)
    failed_result, ok_result = result.value
    assert _result_is_err(failed_result)
    assert isinstance(failed_result.error, RuntimeError)
    assert str(failed_result.error) == "inner fail"
    assert _result_is_ok(ok_result)
    assert ok_result.value == "ok"
    assert "ok:done" in events


def test_race_with_transfer() -> None:
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

    result = run_with_defaults(program())
    assert _result_is_ok(result)
    assert result.value == "fast"
