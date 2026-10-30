from __future__ import annotations

import asyncio
from typing import Any

import doeff
from doeff import (
    Await,
    CompletePromise,
    CreatePromise,
    FailPromise,
    Gather,
    Spawn,
    TaskPriority,
    Wait,
    do,
)
from tests._run_helpers import run_with_defaults


def _result_is_ok(result: Any) -> bool:
    probe = getattr(result, "is_ok", None)
    return bool(probe() if callable(probe) else probe)


def test_task_priority_is_exported_with_its_values() -> None:
    assert doeff.TaskPriority is TaskPriority
    assert TaskPriority.IDLE == 0
    assert TaskPriority.NORMAL == 10
    assert TaskPriority.HIGH == 20


def test_the_old_priority_constants_are_the_task_priorities() -> None:
    # Kept until the repos that read doeff's main use TaskPriority (agora card ki-9f2990a98000).
    assert doeff.PRIORITY_IDLE is TaskPriority.IDLE
    assert doeff.PRIORITY_NORMAL is TaskPriority.NORMAL
    assert doeff.PRIORITY_HIGH is TaskPriority.HIGH


def test_spawn_effect_exposes_priority_field() -> None:
    @do
    def child():
        return "ok"

    default_effect = Spawn(child())
    custom_effect = Spawn(child(), priority=5)

    assert default_effect.priority == TaskPriority.NORMAL
    assert custom_effect.priority == 5



def test_priority_idle_runs_after_normal() -> None:
    events: list[str] = []

    @do
    def child(label: str):
        events.append(label)
        return label

    @do
    def program():
        idle = yield Spawn(child("idle"), priority=TaskPriority.IDLE)
        normal = yield Spawn(child("normal"), priority=TaskPriority.NORMAL)
        _ = yield Gather(idle, normal)
        return tuple(events)

    result = run_with_defaults(program())
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
        t1 = yield Spawn(child("one"), priority=TaskPriority.NORMAL)
        t2 = yield Spawn(child("two"), priority=TaskPriority.NORMAL)
        t3 = yield Spawn(child("three"), priority=TaskPriority.NORMAL)
        _ = yield Gather(t1, t2, t3)
        return tuple(events)

    result = run_with_defaults(program())
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
        task = yield Spawn(normal_task(), priority=TaskPriority.NORMAL)
        events.append("idle-after-spawn")
        return (yield Wait(task))

    @do
    def program():
        idle = yield Spawn(idle_task(), priority=TaskPriority.IDLE)
        return (yield Wait(idle))

    result = run_with_defaults(program())
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
            priority=TaskPriority.IDLE,
        )
        _ = yield Wait(idle_started.future)

        waiter_task_handle = yield Spawn(
            waiter_task(target_promise, waiter_started),
            priority=TaskPriority.NORMAL,
        )
        _ = yield Wait(waiter_started.future)

        yield CompletePromise(release_promise, None)
        _ = yield Wait(idle_task)
        _ = yield Wait(waiter_task_handle)
        return tuple(events)

    result = run_with_defaults(program())
    assert _result_is_ok(result)
    assert result.value == (
        "normal-before-wait",
        "idle-before-complete",
        "normal-after-wait",
        "idle-after-complete",
    )


def test_fail_promise_preempts_if_woken_task_higher_priority() -> None:
    events: list[str] = []

    @do
    def waiter_task(target_promise, waiter_started):
        events.append("normal-before-wait")
        yield CompletePromise(waiter_started, None)
        try:
            _ = yield Wait(target_promise.future)
        except RuntimeError as error:
            events.append("normal-after-wait")
            return str(error)
        raise AssertionError("waiter should observe RuntimeError from failed promise")

    @do
    def idle_failer(target_promise, release_promise, idle_started):
        yield CompletePromise(idle_started, None)
        _ = yield Wait(release_promise.future)
        events.append("idle-before-fail")
        yield FailPromise(target_promise, RuntimeError("boom"))
        events.append("idle-after-fail")
        return "idle-done"

    @do
    def program():
        target_promise = yield CreatePromise()
        release_promise = yield CreatePromise()
        idle_started = yield CreatePromise()
        waiter_started = yield CreatePromise()

        idle_task = yield Spawn(
            idle_failer(target_promise, release_promise, idle_started),
            priority=TaskPriority.IDLE,
        )
        _ = yield Wait(idle_started.future)

        waiter_task_handle = yield Spawn(
            waiter_task(target_promise, waiter_started),
            priority=TaskPriority.NORMAL,
        )
        _ = yield Wait(waiter_started.future)

        yield CompletePromise(release_promise, None)
        _ = yield Wait(idle_task)
        waiter_result = yield Wait(waiter_task_handle)
        return tuple(events), waiter_result

    result = run_with_defaults(program())
    assert _result_is_ok(result)
    assert result.value == (
        (
            "normal-before-wait",
            "idle-before-fail",
            "normal-after-wait",
            "idle-after-fail",
        ),
        "boom",
    )



def test_lower_priority_spawn_does_not_preempt() -> None:
    events: list[str] = []

    @do
    def spawned():
        events.append("idle-runs")
        return "done"

    @do
    def spawner():
        events.append("normal-before")
        task = yield Spawn(spawned(), priority=TaskPriority.IDLE)
        events.append("normal-after")
        return (yield Wait(task))

    @do
    def program():
        task = yield Spawn(spawner(), priority=TaskPriority.NORMAL)
        return (yield Wait(task))

    result = run_with_defaults(program())
    assert _result_is_ok(result)
    assert result.value == "done"
    assert events == ["normal-before", "normal-after", "idle-runs"]


def test_no_one_shot_violation_after_unification() -> None:
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

    result = run_with_defaults(program())
    assert _result_is_ok(result)
    assert result.value == [1, 2, 3]
