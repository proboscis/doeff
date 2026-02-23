from __future__ import annotations

import asyncio
from typing import Any

import pytest

import doeff
from doeff import (
    PRIORITY_HIGH,
    PRIORITY_IDLE,
    PRIORITY_NORMAL,
    Await,
    CompletePromise,
    CreatePromise,
    Gather,
    Spawn,
    Wait,
    async_run,
    default_async_handlers,
    default_handlers,
    do,
    run,
)


def _result_is_ok(result: Any) -> bool:
    probe = getattr(result, "is_ok", None)
    return bool(probe() if callable(probe) else probe)


def test_priority_constants_exported() -> None:
    assert hasattr(doeff, "PRIORITY_IDLE")
    assert hasattr(doeff, "PRIORITY_NORMAL")
    assert hasattr(doeff, "PRIORITY_HIGH")
    assert PRIORITY_IDLE == 0
    assert PRIORITY_NORMAL == 10
    assert PRIORITY_HIGH == 20


def test_spawn_effect_exposes_priority_field() -> None:
    @do
    def child():
        return "ok"

    default_effect = Spawn(child())
    custom_effect = Spawn(child(), priority=5)

    assert default_effect.priority == PRIORITY_NORMAL
    assert custom_effect.priority == 5


def test_priority_high_runs_before_normal() -> None:
    events: list[str] = []

    @do
    def child(label: str):
        events.append(label)
        return label

    @do
    def program():
        normal = yield Spawn(child("normal"), priority=PRIORITY_NORMAL)
        high = yield Spawn(child("high"), priority=PRIORITY_HIGH)
        _ = yield Gather(normal, high)
        return tuple(events)

    result = run(program(), handlers=default_handlers())
    assert _result_is_ok(result)
    assert result.value == ("high", "normal")


def test_priority_idle_runs_after_normal() -> None:
    events: list[str] = []

    @do
    def child(label: str):
        events.append(label)
        return label

    @do
    def program():
        idle = yield Spawn(child("idle"), priority=PRIORITY_IDLE)
        normal = yield Spawn(child("normal"), priority=PRIORITY_NORMAL)
        _ = yield Gather(idle, normal)
        return tuple(events)

    result = run(program(), handlers=default_handlers())
    assert _result_is_ok(result)
    assert result.value == ("normal", "idle")


def test_deterministic_ordering_within_priority() -> None:
    events: list[str] = []

    @do
    def child(label: str):
        events.append(label)
        return label

    @do
    def program():
        t1 = yield Spawn(child("one"), priority=PRIORITY_NORMAL)
        t2 = yield Spawn(child("two"), priority=PRIORITY_NORMAL)
        t3 = yield Spawn(child("three"), priority=PRIORITY_NORMAL)
        _ = yield Gather(t1, t2, t3)
        return tuple(events)

    result = run(program(), handlers=default_handlers())
    assert _result_is_ok(result)
    assert result.value == ("one", "two", "three")


def test_spawn_higher_priority_preempts_current() -> None:
    events: list[str] = []

    @do
    def normal_task():
        events.append("normal-runs")
        return "done"

    @do
    def idle_task():
        events.append("idle-before-spawn")
        task = yield Spawn(normal_task(), priority=PRIORITY_NORMAL)
        events.append("idle-after-spawn")
        return (yield Wait(task))

    @do
    def program():
        idle = yield Spawn(idle_task(), priority=PRIORITY_IDLE)
        return (yield Wait(idle))

    result = run(program(), handlers=default_handlers())
    assert _result_is_ok(result)
    assert result.value == "done"
    assert events == ["idle-before-spawn", "normal-runs", "idle-after-spawn"]


def test_complete_promise_preempts_if_woken_task_higher_priority() -> None:
    events: list[str] = []

    @do
    def waiter_task(target_promise, waiter_started):
        events.append("normal-before-wait")
        yield CompletePromise(waiter_started, None)
        value = yield Wait(target_promise.future)
        events.append("normal-after-wait")
        return value

    @do
    def idle_completer(target_promise, release_promise, idle_started):
        yield CompletePromise(idle_started, None)
        _ = yield Wait(release_promise.future)
        events.append("idle-before-complete")
        yield CompletePromise(target_promise, "done")
        events.append("idle-after-complete")
        return "idle-done"

    @do
    def program():
        target_promise = yield CreatePromise()
        release_promise = yield CreatePromise()
        idle_started = yield CreatePromise()
        waiter_started = yield CreatePromise()

        idle_task = yield Spawn(
            idle_completer(target_promise, release_promise, idle_started),
            priority=PRIORITY_IDLE,
        )
        _ = yield Wait(idle_started.future)

        waiter_task_handle = yield Spawn(
            waiter_task(target_promise, waiter_started),
            priority=PRIORITY_NORMAL,
        )
        _ = yield Wait(waiter_started.future)

        yield CompletePromise(release_promise, None)
        _ = yield Wait(idle_task)
        _ = yield Wait(waiter_task_handle)
        return tuple(events)

    result = run(program(), handlers=default_handlers())
    assert _result_is_ok(result)
    assert result.value == (
        "normal-before-wait",
        "idle-before-complete",
        "normal-after-wait",
        "idle-after-complete",
    )


def test_same_priority_spawn_does_not_preempt() -> None:
    events: list[str] = []

    @do
    def spawned():
        events.append("spawned-runs")
        return "done"

    @do
    def spawner():
        events.append("spawner-before")
        task = yield Spawn(spawned(), priority=PRIORITY_NORMAL)
        events.append("spawner-after")
        return (yield Wait(task))

    @do
    def program():
        task = yield Spawn(spawner(), priority=PRIORITY_NORMAL)
        return (yield Wait(task))

    result = run(program(), handlers=default_handlers())
    assert _result_is_ok(result)
    assert result.value == "done"
    assert events == ["spawner-before", "spawner-after", "spawned-runs"]


def test_lower_priority_spawn_does_not_preempt() -> None:
    events: list[str] = []

    @do
    def spawned():
        events.append("idle-runs")
        return "done"

    @do
    def spawner():
        events.append("normal-before")
        task = yield Spawn(spawned(), priority=PRIORITY_IDLE)
        events.append("normal-after")
        return (yield Wait(task))

    @do
    def program():
        task = yield Spawn(spawner(), priority=PRIORITY_NORMAL)
        return (yield Wait(task))

    result = run(program(), handlers=default_handlers())
    assert _result_is_ok(result)
    assert result.value == "done"
    assert events == ["normal-before", "normal-after", "idle-runs"]


@pytest.mark.asyncio
async def test_no_one_shot_violation_after_unification() -> None:
    @do
    def worker(n: int):
        _ = yield Await(asyncio.sleep(0))
        return n

    @do
    def program():
        t1 = yield Spawn(worker(1))
        t2 = yield Spawn(worker(2))
        t3 = yield Spawn(worker(3))
        return (yield Gather(t1, t2, t3))

    result = await async_run(program(), handlers=default_async_handlers())
    assert _result_is_ok(result)
    assert result.value == [1, 2, 3]
