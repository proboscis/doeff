from __future__ import annotations

from typing import Any

import pytest

from doeff import Delegate, Gather, Pass, Resume, SpawnEffect, Task, WithHandler, do, race
from doeff.effects.spawn import coerce_task_handle, spawn_intercept_handler


def _capture_raw_spawn_handle(observed: dict[str, Any]):
    def _handler(effect, k):
        if isinstance(effect, SpawnEffect):
            raw = yield Delegate()
            observed["raw"] = raw
            return (yield Resume(k, raw))
        yield Pass()

    return _handler


def _raw_spawn(program: Any) -> SpawnEffect:
    return SpawnEffect(program=program, options={}, store_mode="isolated")


def _with_spawn_intercepts(program: Any, *layers: Any) -> Any:
    wrapped = WithHandler(spawn_intercept_handler, program)
    for layer in layers:
        wrapped = WithHandler(layer, wrapped)
    return wrapped


def _tag_spawn_handle(tag: str):
    def _handler(effect, k):
        if isinstance(effect, SpawnEffect):
            raw = yield Delegate()
            task = coerce_task_handle(raw)
            tagged = dict(task._handle)
            trail = list(tagged.get("trail", []))
            trail.append(tag)
            tagged["trail"] = trail
            return (yield Resume(k, tagged))
        yield Pass()

    return _handler


@pytest.mark.asyncio
async def test_spawn_intercept_transforms_task_handle(parameterized_interpreter) -> None:
    observed: dict[str, Any] = {}

    @do
    def child():
        return "ok"

    @do
    def program():
        task = yield _raw_spawn(child())
        values = yield Gather(task)
        return task, next(iter(values))

    wrapped = _with_spawn_intercepts(program(), _capture_raw_spawn_handle(observed))
    result = await parameterized_interpreter.run_async(wrapped)
    task, child_value = result.value

    assert isinstance(task, Task)
    assert child_value == "ok"
    assert isinstance(observed.get("raw"), dict)
    assert observed["raw"].get("type") == "Task"
    assert task._handle == observed["raw"]


@pytest.mark.asyncio
async def test_spawn_intercept_plus_gather_uses_coerced_handles(parameterized_interpreter) -> None:
    @do
    def leaf(value: int):
        return value

    @do
    def child(value: int):
        return (yield _raw_spawn(leaf(value)))

    @do
    def program():
        t1 = yield _raw_spawn(child(1))
        t2 = yield _raw_spawn(child(2))
        t3 = yield _raw_spawn(child(3))
        nested_handles = yield Gather(t1, t2, t3)
        values = yield Gather(*nested_handles)
        return nested_handles, values

    result = await parameterized_interpreter.run_async(_with_spawn_intercepts(program()))
    nested_handles, values = result.value

    assert all(isinstance(item, Task) for item in nested_handles)
    assert tuple(values) == (1, 2, 3)


@pytest.mark.asyncio
async def test_spawn_intercept_plus_race_uses_coerced_handle(parameterized_interpreter) -> None:
    @do
    def leaf(label: str):
        return label

    @do
    def child(label: str):
        return (yield _raw_spawn(leaf(label)))

    @do
    def program():
        t1 = yield _raw_spawn(child("left"))
        t2 = yield _raw_spawn(child("right"))
        winner = yield race(t1, t2)
        winner_value = yield Gather(winner)
        return winner, winner_value

    result = await parameterized_interpreter.run_async(_with_spawn_intercepts(program()))
    winner, winner_value = result.value

    assert isinstance(winner, Task)
    assert next(iter(winner_value)) in {"left", "right"}


@pytest.mark.asyncio
async def test_spawn_intercept_applies_to_nested_spawns(parameterized_interpreter) -> None:
    layer = _tag_spawn_handle("nested")

    @do
    def leaf():
        return "leaf"

    @do
    def child():
        return (yield _raw_spawn(leaf()))

    @do
    def program():
        child_task = yield _raw_spawn(child())
        nested_handles = yield Gather(child_task)
        nested_task = next(iter(nested_handles))
        nested_values = yield Gather(nested_task)
        return nested_task, next(iter(nested_values))

    wrapped = _with_spawn_intercepts(program(), layer)
    result = await parameterized_interpreter.run_async(wrapped)
    nested_task, nested_value = result.value

    assert isinstance(nested_task, Task)
    assert nested_value == "leaf"
    assert nested_task._handle.get("trail") == ["nested"]


@pytest.mark.asyncio
async def test_spawn_intercept_multiple_layers_transform_in_order(parameterized_interpreter) -> None:
    outer = _tag_spawn_handle("outer")
    inner = _tag_spawn_handle("inner")

    @do
    def child():
        return "ok"

    @do
    def program():
        task = yield _raw_spawn(child())
        values = yield Gather(task)
        return task, next(iter(values))

    wrapped = _with_spawn_intercepts(program(), inner, outer)
    result = await parameterized_interpreter.run_async(wrapped)
    task, child_value = result.value

    assert isinstance(task, Task)
    assert child_value == "ok"
    assert task._handle.get("trail") == ["outer", "inner"]
