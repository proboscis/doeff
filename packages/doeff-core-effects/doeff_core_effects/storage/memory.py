"""
In-memory storage implementation for testing.

This storage is NOT durable across process restarts.
Use SQLiteStorage for production durable workflows.
"""


import threading
from collections.abc import Iterable
from typing import Any


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

    def get(self, key: str) -> Any | None:
        """Get value by key. Returns None if not found."""
        with self._lock:
            return self._data.get(key)

    def put(self, key: str, value: Any) -> None:
        """Store value with key. Overwrites if exists."""
        with self._lock:
            self._data[key] = value

    def delete(self, key: str) -> bool:
        """Delete key. Returns True if key existed."""
        with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

    def exists(self, key: str) -> bool:
        """Check if key exists."""
        with self._lock:
            return key in self._data

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
