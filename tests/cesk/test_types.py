"""Unit tests for doeff.cesk.types module."""

from __future__ import annotations

import pytest
from doeff._vendor import FrozenDict
from doeff.cesk.types import Environment, FutureId, SpawnId, Store, TaskId


class TestTaskId:
    """Tests for TaskId type."""

    def test_create_task_id(self) -> None:
        """TaskId can be created with an integer value."""
        task_id = TaskId(1)
        assert task_id.value == 1

    def test_task_id_str(self) -> None:
        """TaskId has a readable string representation."""
        task_id = TaskId(42)
        assert str(task_id) == "task-42"

    def test_task_id_equality(self) -> None:
        """TaskIds with same value are equal."""
        t1 = TaskId(1)
        t2 = TaskId(1)
        t3 = TaskId(2)
        assert t1 == t2
        assert t1 != t3

    def test_task_id_hashable(self) -> None:
        """TaskId can be used as dict key or in set."""
        t1 = TaskId(1)
        t2 = TaskId(1)
        t3 = TaskId(2)

        # Can be used in set
        s = {t1, t2, t3}
        assert len(s) == 2

        # Can be used as dict key
        d = {t1: "task1", t3: "task2"}
        assert d[t2] == "task1"  # t1 == t2

    def test_task_id_immutable(self) -> None:
        """TaskId is immutable (frozen dataclass)."""
        task_id = TaskId(1)
        with pytest.raises(AttributeError):
            task_id.value = 2  # type: ignore[misc]


class TestFutureId:
    """Tests for FutureId type."""

    def test_create_future_id(self) -> None:
        """FutureId can be created with an integer value."""
        future_id = FutureId(1)
        assert future_id.value == 1

    def test_future_id_str(self) -> None:
        """FutureId has a readable string representation."""
        future_id = FutureId(42)
        assert str(future_id) == "future-42"

    def test_future_id_equality(self) -> None:
        """FutureIds with same value are equal."""
        f1 = FutureId(1)
        f2 = FutureId(1)
        f3 = FutureId(2)
        assert f1 == f2
        assert f1 != f3

    def test_future_id_hashable(self) -> None:
        """FutureId can be used as dict key or in set."""
        f1 = FutureId(1)
        f2 = FutureId(1)
        f3 = FutureId(2)

        s = {f1, f2, f3}
        assert len(s) == 2

        d = {f1: "future1", f3: "future2"}
        assert d[f2] == "future1"

    def test_future_id_immutable(self) -> None:
        """FutureId is immutable (frozen dataclass)."""
        future_id = FutureId(1)
        with pytest.raises(AttributeError):
            future_id.value = 2  # type: ignore[misc]


class TestSpawnId:
    """Tests for SpawnId type."""

    def test_create_spawn_id(self) -> None:
        """SpawnId can be created with an integer value."""
        spawn_id = SpawnId(1)
        assert spawn_id.value == 1

    def test_spawn_id_str(self) -> None:
        """SpawnId has a readable string representation."""
        spawn_id = SpawnId(42)
        assert str(spawn_id) == "spawn-42"

    def test_spawn_id_equality(self) -> None:
        """SpawnIds with same value are equal."""
        s1 = SpawnId(1)
        s2 = SpawnId(1)
        s3 = SpawnId(2)
        assert s1 == s2
        assert s1 != s3

    def test_spawn_id_hashable(self) -> None:
        """SpawnId can be used as dict key or in set."""
        s1 = SpawnId(1)
        s2 = SpawnId(1)
        s3 = SpawnId(2)

        s = {s1, s2, s3}
        assert len(s) == 2

        d = {s1: "spawn1", s3: "spawn2"}
        assert d[s2] == "spawn1"

    def test_spawn_id_immutable(self) -> None:
        """SpawnId is immutable (frozen dataclass)."""
        spawn_id = SpawnId(1)
        with pytest.raises(AttributeError):
            spawn_id.value = 2  # type: ignore[misc]


class TestIdTypeSafety:
    """Tests for type safety between different ID types."""

    def test_different_id_types_not_equal(self) -> None:
        """Different ID types with same value are not equal."""
        task_id = TaskId(1)
        future_id = FutureId(1)
        spawn_id = SpawnId(1)

        # Different types should not be equal
        assert task_id != future_id
        assert task_id != spawn_id
        assert future_id != spawn_id


class TestEnvironment:
    """Tests for Environment type alias."""

    def test_environment_is_frozen_dict(self) -> None:
        """Environment is a FrozenDict alias."""
        env: Environment = FrozenDict({"key": "value"})
        assert isinstance(env, FrozenDict)
        assert env["key"] == "value"

    def test_environment_immutable(self) -> None:
        """Environment cannot be mutated."""
        env: Environment = FrozenDict({"key": "value"})
        with pytest.raises(TypeError):
            env["key"] = "new_value"  # type: ignore[index]

    def test_environment_copy_on_write(self) -> None:
        """Environment supports copy-on-write via | operator."""
        env1: Environment = FrozenDict({"a": 1})
        env2: Environment = env1 | FrozenDict({"b": 2})

        assert env1 == FrozenDict({"a": 1})  # Original unchanged
        assert env2 == FrozenDict({"a": 1, "b": 2})  # New with both


class TestStore:
    """Tests for Store type alias."""

    def test_store_is_dict(self) -> None:
        """Store is a regular dict."""
        store: Store = {"key": "value"}
        assert isinstance(store, dict)
        assert store["key"] == "value"

    def test_store_mutable(self) -> None:
        """Store can be mutated."""
        store: Store = {"key": "value"}
        store["key"] = "new_value"
        store["other"] = 42
        assert store == {"key": "new_value", "other": 42}

    def test_store_reserved_keys(self) -> None:
        """Store can hold reserved keys used by CESK machine."""
        store: Store = {
            "__log__": [],
            "__memo__": {},
            "__cache_storage__": {},
            "__dispatcher__": None,
        }
        assert "__log__" in store
        assert "__memo__" in store
        assert "__cache_storage__" in store
        assert "__dispatcher__" in store
