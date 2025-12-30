"""
Tests for durable storage backends.

Test IDs from issue spec:
- T1: cacheput then cacheget same key - Returns cached value
- T2: cacheget non-existent key - Returns None
- T3: cacheput overwrites existing - New value returned
- T4: cachedelete existing key - Returns True, key gone
- T5: cacheexists existing/non-existing - True/False
- T6: SQLite persists across connections - Value survives reconnect
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from doeff.storage import DurableStorage, InMemoryStorage, SQLiteStorage


class TestInMemoryStorage:
    """Tests for InMemoryStorage backend."""

    def test_put_then_get_same_key(self) -> None:
        """T1: cacheput then cacheget same key returns cached value."""
        storage = InMemoryStorage()
        storage.put("test_key", {"data": 42, "name": "test"})
        result = storage.get("test_key")
        assert result == {"data": 42, "name": "test"}

    def test_get_nonexistent_key(self) -> None:
        """T2: cacheget non-existent key returns None."""
        storage = InMemoryStorage()
        result = storage.get("nonexistent")
        assert result is None

    def test_put_overwrites_existing(self) -> None:
        """T3: cacheput overwrites existing value."""
        storage = InMemoryStorage()
        storage.put("key", "old_value")
        storage.put("key", "new_value")
        result = storage.get("key")
        assert result == "new_value"

    def test_delete_existing_key(self) -> None:
        """T4: cachedelete existing key returns True, key gone."""
        storage = InMemoryStorage()
        storage.put("key", "value")
        deleted = storage.delete("key")
        assert deleted is True
        assert storage.get("key") is None

    def test_delete_nonexistent_key(self) -> None:
        """T4: cachedelete non-existent key returns False."""
        storage = InMemoryStorage()
        deleted = storage.delete("nonexistent")
        assert deleted is False

    def test_exists_existing_key(self) -> None:
        """T5: cacheexists on existing key returns True."""
        storage = InMemoryStorage()
        storage.put("key", "value")
        assert storage.exists("key") is True

    def test_exists_nonexistent_key(self) -> None:
        """T5: cacheexists on non-existent key returns False."""
        storage = InMemoryStorage()
        assert storage.exists("nonexistent") is False

    def test_keys(self) -> None:
        """keys() returns all stored keys."""
        storage = InMemoryStorage()
        storage.put("a", 1)
        storage.put("b", 2)
        storage.put("c", 3)
        keys = set(storage.keys())
        assert keys == {"a", "b", "c"}

    def test_items(self) -> None:
        """items() returns all key-value pairs."""
        storage = InMemoryStorage()
        storage.put("a", 1)
        storage.put("b", 2)
        items = dict(storage.items())
        assert items == {"a": 1, "b": 2}

    def test_clear(self) -> None:
        """clear() removes all entries."""
        storage = InMemoryStorage()
        storage.put("a", 1)
        storage.put("b", 2)
        storage.clear()
        assert len(storage) == 0
        assert list(storage.keys()) == []

    def test_len(self) -> None:
        """__len__ returns correct count."""
        storage = InMemoryStorage()
        assert len(storage) == 0
        storage.put("a", 1)
        assert len(storage) == 1
        storage.put("b", 2)
        assert len(storage) == 2


class TestSQLiteStorage:
    """Tests for SQLiteStorage backend."""

    def test_put_then_get_same_key(self, tmp_path: Path) -> None:
        """T1: cacheput then cacheget same key returns cached value."""
        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(db_path)
        storage.put("test_key", {"data": 42, "name": "test"})
        result = storage.get("test_key")
        assert result == {"data": 42, "name": "test"}
        storage.close()

    def test_get_nonexistent_key(self, tmp_path: Path) -> None:
        """T2: cacheget non-existent key returns None."""
        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(db_path)
        result = storage.get("nonexistent")
        assert result is None
        storage.close()

    def test_put_overwrites_existing(self, tmp_path: Path) -> None:
        """T3: cacheput overwrites existing value."""
        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(db_path)
        storage.put("key", "old_value")
        storage.put("key", "new_value")
        result = storage.get("key")
        assert result == "new_value"
        storage.close()

    def test_delete_existing_key(self, tmp_path: Path) -> None:
        """T4: cachedelete existing key returns True, key gone."""
        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(db_path)
        storage.put("key", "value")
        deleted = storage.delete("key")
        assert deleted is True
        assert storage.get("key") is None
        storage.close()

    def test_delete_nonexistent_key(self, tmp_path: Path) -> None:
        """T4: cachedelete non-existent key returns False."""
        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(db_path)
        deleted = storage.delete("nonexistent")
        assert deleted is False
        storage.close()

    def test_exists_existing_key(self, tmp_path: Path) -> None:
        """T5: cacheexists on existing key returns True."""
        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(db_path)
        storage.put("key", "value")
        assert storage.exists("key") is True
        storage.close()

    def test_exists_nonexistent_key(self, tmp_path: Path) -> None:
        """T5: cacheexists on non-existent key returns False."""
        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(db_path)
        assert storage.exists("nonexistent") is False
        storage.close()

    def test_persists_across_connections(self, tmp_path: Path) -> None:
        """T6: SQLite persists across connections."""
        db_path = tmp_path / "test.db"

        # Write with first connection
        storage1 = SQLiteStorage(db_path)
        storage1.put("persistent_key", {"complex": [1, 2, 3], "nested": {"a": 1}})
        storage1.close()

        # Read with new connection
        storage2 = SQLiteStorage(db_path)
        result = storage2.get("persistent_key")
        assert result == {"complex": [1, 2, 3], "nested": {"a": 1}}
        storage2.close()

    def test_memory_database(self) -> None:
        """In-memory SQLite database works."""
        storage = SQLiteStorage(":memory:")
        storage.put("key", "value")
        assert storage.get("key") == "value"
        storage.close()

    def test_keys(self, tmp_path: Path) -> None:
        """keys() returns all stored keys."""
        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(db_path)
        storage.put("a", 1)
        storage.put("b", 2)
        storage.put("c", 3)
        keys = set(storage.keys())
        assert keys == {"a", "b", "c"}
        storage.close()

    def test_items(self, tmp_path: Path) -> None:
        """items() returns all key-value pairs."""
        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(db_path)
        storage.put("a", 1)
        storage.put("b", 2)
        items = dict(storage.items())
        assert items == {"a": 1, "b": 2}
        storage.close()

    def test_clear(self, tmp_path: Path) -> None:
        """clear() removes all entries."""
        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(db_path)
        storage.put("a", 1)
        storage.put("b", 2)
        storage.clear()
        assert len(storage) == 0
        assert list(storage.keys()) == []
        storage.close()

    def test_complex_values(self, tmp_path: Path) -> None:
        """Complex Python objects can be stored and retrieved."""
        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(db_path)

        # Test various types
        storage.put("list", [1, 2, 3, "test"])
        storage.put("dict", {"nested": {"deep": True}})
        storage.put("tuple", (1, 2, 3))
        storage.put("none", None)
        storage.put("float", 3.14159)

        assert storage.get("list") == [1, 2, 3, "test"]
        assert storage.get("dict") == {"nested": {"deep": True}}
        assert storage.get("tuple") == (1, 2, 3)
        assert storage.get("none") is None
        assert storage.get("float") == 3.14159
        storage.close()


class TestDurableStorageProtocol:
    """Tests that storage implementations satisfy the protocol by testing method contracts."""

    def test_inmemory_satisfies_protocol(self) -> None:
        """InMemoryStorage satisfies DurableStorage protocol by testing all methods."""
        storage = InMemoryStorage()

        # Test isinstance for runtime-checkable protocol
        assert isinstance(storage, DurableStorage)

        # Test actual method contracts
        storage.put("key", "value")
        assert storage.get("key") == "value"
        assert storage.exists("key") is True
        assert "key" in list(storage.keys())
        assert ("key", "value") in list(storage.items())
        assert storage.delete("key") is True
        assert storage.exists("key") is False
        storage.put("a", 1)
        storage.put("b", 2)
        storage.clear()
        assert list(storage.keys()) == []

    def test_sqlite_satisfies_protocol(self, tmp_path: Path) -> None:
        """SQLiteStorage satisfies DurableStorage protocol by testing all methods."""
        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(db_path)

        # Test isinstance for runtime-checkable protocol
        assert isinstance(storage, DurableStorage)

        # Test actual method contracts
        storage.put("key", "value")
        assert storage.get("key") == "value"
        assert storage.exists("key") is True
        assert "key" in list(storage.keys())
        assert ("key", "value") in list(storage.items())
        assert storage.delete("key") is True
        assert storage.exists("key") is False
        storage.put("a", 1)
        storage.put("b", 2)
        storage.clear()
        assert list(storage.keys()) == []
        storage.close()
