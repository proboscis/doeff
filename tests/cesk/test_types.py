"""Tests for CESK types module."""

import pytest

from doeff._vendor import FrozenDict
from doeff.cesk.types import (
    Environment,
    FutureHandle,
    FutureId,
    SpawnHandle,
    SpawnId,
    Store,
    TaskHandle,
    TaskId,
    empty_environment,
    empty_store,
)


class TestTaskId:
    """Tests for TaskId."""

    def test_create_unique(self) -> None:
        """TaskId.new() creates unique identifiers."""
        id1 = TaskId.new()
        id2 = TaskId.new()
        assert id1 != id2

    def test_equality(self) -> None:
        """TaskId equality is based on internal UUID."""
        id1 = TaskId.new()
        id2 = TaskId.new()
        assert id1 == id1
        assert id1 != id2

    def test_hashable(self) -> None:
        """TaskId can be used as dict key."""
        id1 = TaskId.new()
        id2 = TaskId.new()
        d: dict[TaskId, str] = {id1: "first", id2: "second"}
        assert d[id1] == "first"
        assert d[id2] == "second"

    def test_comparable(self) -> None:
        """TaskId supports ordering."""
        ids = [TaskId.new() for _ in range(5)]
        sorted_ids = sorted(ids)
        assert len(sorted_ids) == 5

    def test_str_repr(self) -> None:
        """TaskId has readable string representations."""
        task_id = TaskId.new()
        assert str(task_id).startswith("task-")
        assert "TaskId(" in repr(task_id)


class TestFutureId:
    """Tests for FutureId."""

    def test_create_unique(self) -> None:
        """FutureId.new() creates unique identifiers."""
        id1 = FutureId.new()
        id2 = FutureId.new()
        assert id1 != id2

    def test_hashable(self) -> None:
        """FutureId can be used as dict key."""
        id1 = FutureId.new()
        d: dict[FutureId, str] = {id1: "value"}
        assert d[id1] == "value"

    def test_str_repr(self) -> None:
        """FutureId has readable string representations."""
        future_id = FutureId.new()
        assert str(future_id).startswith("future-")
        assert "FutureId(" in repr(future_id)


class TestSpawnId:
    """Tests for SpawnId."""

    def test_create_unique(self) -> None:
        """SpawnId.new() creates unique identifiers."""
        id1 = SpawnId.new()
        id2 = SpawnId.new()
        assert id1 != id2

    def test_hashable(self) -> None:
        """SpawnId can be used as dict key."""
        id1 = SpawnId.new()
        d: dict[SpawnId, str] = {id1: "value"}
        assert d[id1] == "value"

    def test_str_repr(self) -> None:
        """SpawnId has readable string representations."""
        spawn_id = SpawnId.new()
        assert str(spawn_id).startswith("spawn-")
        assert "SpawnId(" in repr(spawn_id)


class TestHandles:
    """Tests for Handle types."""

    def test_task_handle(self) -> None:
        """TaskHandle wraps a TaskId."""
        task_id = TaskId.new()
        handle = TaskHandle(task_id)
        assert handle.task_id == task_id
        assert "TaskHandle" in str(handle)

    def test_future_handle(self) -> None:
        """FutureHandle wraps a FutureId."""
        future_id = FutureId.new()
        handle = FutureHandle(future_id)
        assert handle.future_id == future_id
        assert "FutureHandle" in str(handle)

    def test_spawn_handle(self) -> None:
        """SpawnHandle wraps a SpawnId."""
        spawn_id = SpawnId.new()
        handle = SpawnHandle(spawn_id)
        assert handle.spawn_id == spawn_id
        assert "SpawnHandle" in str(handle)


class TestEnvironment:
    """Tests for Environment type."""

    def test_empty_environment(self) -> None:
        """empty_environment() returns empty FrozenDict."""
        env = empty_environment()
        assert isinstance(env, FrozenDict)
        assert len(env) == 0

    def test_immutable(self) -> None:
        """Environment is immutable."""
        env: Environment = FrozenDict({"key": "value"})
        # FrozenDict should not support item assignment
        with pytest.raises(TypeError):
            env["key"] = "new_value"  # type: ignore

    def test_copy_on_write(self) -> None:
        """Environment supports copy-on-write via | operator."""
        env1: Environment = FrozenDict({"a": 1})
        env2: Environment = env1 | FrozenDict({"b": 2})
        assert env1["a"] == 1
        assert "b" not in env1
        assert env2["a"] == 1
        assert env2["b"] == 2


class TestStore:
    """Tests for Store type."""

    def test_empty_store(self) -> None:
        """empty_store() returns empty dict."""
        store = empty_store()
        assert isinstance(store, dict)
        assert len(store) == 0

    def test_mutable(self) -> None:
        """Store is mutable."""
        store: Store = empty_store()
        store["key"] = "value"
        assert store["key"] == "value"
        store["key"] = "new_value"
        assert store["key"] == "new_value"
