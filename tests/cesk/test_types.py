from __future__ import annotations

import pytest

from doeff.cesk.types import (
    TaskId,
    FutureId,
    SpawnId,
    TaskIdGenerator,
    FutureIdGenerator,
    SpawnIdGenerator,
    Environment,
    Store,
)
from doeff._vendor import FrozenDict


def test_task_id_is_newtype():
    task_id = TaskId(1)
    assert task_id == 1


def test_future_id_is_newtype():
    future_id = FutureId(2)
    assert future_id == 2


def test_spawn_id_is_newtype():
    spawn_id = SpawnId(3)
    assert spawn_id == 3


def test_task_id_generator_next():
    gen = TaskIdGenerator()
    task_id1, gen = gen.next()
    task_id2, gen = gen.next()
    task_id3, gen = gen.next()
    
    assert task_id1 == TaskId(0)
    assert task_id2 == TaskId(1)
    assert task_id3 == TaskId(2)


def test_future_id_generator_next():
    gen = FutureIdGenerator()
    future_id1, gen = gen.next()
    future_id2, gen = gen.next()
    future_id3, gen = gen.next()
    
    assert future_id1 == FutureId(0)
    assert future_id2 == FutureId(1)
    assert future_id3 == FutureId(2)


def test_spawn_id_generator_next():
    gen = SpawnIdGenerator()
    spawn_id1, gen = gen.next()
    spawn_id2, gen = gen.next()
    spawn_id3, gen = gen.next()
    
    assert spawn_id1 == SpawnId(0)
    assert spawn_id2 == SpawnId(1)
    assert spawn_id3 == SpawnId(2)


def test_task_id_generator_immutability():
    gen1 = TaskIdGenerator()
    task_id1, gen2 = gen1.next()
    task_id2, gen3 = gen1.next()
    
    assert task_id1 == task_id2 == TaskId(0)
    assert gen2._counter == 1
    assert gen3._counter == 1


def test_environment_is_frozen_dict():
    env: Environment = FrozenDict({"key": "value"})
    assert env["key"] == "value"
    assert isinstance(env, FrozenDict)


def test_store_is_dict():
    store: Store = {"key": "value", "__log__": []}
    assert store["key"] == "value"
    assert store["__log__"] == []
    assert isinstance(store, dict)


def test_environment_immutability():
    env: Environment = FrozenDict({"key": "value"})
    
    with pytest.raises((TypeError, AttributeError)):
        env["key"] = "new_value"  # type: ignore


def test_store_mutability():
    store: Store = {"key": "value"}
    store["key"] = "new_value"
    assert store["key"] == "new_value"


def test_id_generators_are_frozen():
    task_gen = TaskIdGenerator()
    future_gen = FutureIdGenerator()
    spawn_gen = SpawnIdGenerator()
    
    with pytest.raises((AttributeError, TypeError)):
        task_gen._counter = 100  # type: ignore
    
    with pytest.raises((AttributeError, TypeError)):
        future_gen._counter = 100  # type: ignore
    
    with pytest.raises((AttributeError, TypeError)):
        spawn_gen._counter = 100  # type: ignore
