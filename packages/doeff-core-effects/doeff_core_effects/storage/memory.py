"""
In-memory storage implementation for testing.

This storage is NOT durable across process restarts.
Use SQLiteStorage for production durable workflows.
"""


import asyncio
import threading
from collections.abc import Iterable
from typing import Any

from doeff_core_effects.effects import Await


class InMemoryStorage:
    """
    In-memory storage for testing. Not durable across restarts.

    Thread-safe via a reentrant lock for concurrent access.

    Example:
        storage = InMemoryStorage()
        storage.put("key", {"data": 123})
        value = storage.get("key")  # {"data": 123}
    """

    def __init__(self) -> None:
        """Initialize in-memory storage with empty dict."""
        self._data: dict[str, Any] = {}
        self._lock = threading.RLock()

    def _sync_get(self, key: str) -> Any | None:
        with self._lock:
            return self._data.get(key)

    def _sync_put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def _sync_delete(self, key: str) -> bool:
        with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

    def _sync_exists(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def get(self, key: str):
        """Get value by key. Returns Program[Any | None] via Await."""
        return Await(asyncio.to_thread(self._sync_get, key))

    def put(self, key: str, value: Any):
        """Store value with key. Returns Program[None] via Await."""
        return Await(asyncio.to_thread(self._sync_put, key, value))

    def delete(self, key: str):
        """Delete key. Returns Program[bool] via Await."""
        return Await(asyncio.to_thread(self._sync_delete, key))

    def exists(self, key: str):
        """Check if key exists. Returns Program[bool] via Await."""
        return Await(asyncio.to_thread(self._sync_exists, key))

    def keys(self) -> Iterable[str]:
        """Return list of all keys."""
        with self._lock:
            return list(self._data.keys())

    def items(self) -> Iterable[tuple[str, Any]]:
        """Return list of all (key, value) pairs."""
        with self._lock:
            return list(self._data.items())

    def clear(self) -> None:
        """Delete all entries."""
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        """Return number of entries."""
        with self._lock:
            return len(self._data)

    def __repr__(self) -> str:
        with self._lock:
            return f"InMemoryStorage({len(self._data)} entries)"
