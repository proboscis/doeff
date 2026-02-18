from __future__ import annotations

import asyncio
from typing import Any

import pytest

from doeff import (
    Await,
    Gather,
    Spawn,
    Wait,
    async_run,
    default_async_handlers,
    default_handlers,
    do,
    race,
    run,
)


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
