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
    Gather,
    Spawn,
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
