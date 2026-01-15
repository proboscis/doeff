"""
Durable storage interfaces and implementations for doeff workflows.

This module provides the storage abstraction layer for durable workflow execution.
The storage is used by cache effects (CacheGet/CachePut) to persist operation results
across process restarts.

Public API:
- DurableStorage: Protocol for storage backends
- InMemoryStorage: In-memory storage (for testing, not durable)
- SQLiteStorage: SQLite-backed persistent storage

Example usage:
    from doeff.storage import SQLiteStorage
    from doeff.cesk import run_sync

    result = run_sync(
        my_workflow(),
        storage=SQLiteStorage("workflow.db"),
    )
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DurableStorage(Protocol):
    """
    Protocol for durable storage backends.

    Implementations must be thread-safe for concurrent access.
    Values are opaque - the storage layer handles serialization.

    Methods:
        get: Retrieve value by key. Returns None if not found.
        put: Store value with key. Overwrites if exists.
        delete: Delete key. Returns True if key existed.
        exists: Check if key exists.
        keys: Iterate over all keys.
        items: Iterate over all (key, value) pairs.
        clear: Delete all entries.
    """

    def get(self, key: str) -> Any | None:
        """Get value by key. Returns None if not found."""
        ...

    def put(self, key: str, value: Any) -> None:
        """Store value with key. Overwrites if exists."""
        ...

    def delete(self, key: str) -> bool:
        """Delete key. Returns True if key existed."""
        ...

    def exists(self, key: str) -> bool:
        """Check if key exists."""
        ...

    def keys(self) -> Iterable[str]:
        """Iterate over all keys."""
        ...

    def items(self) -> Iterable[tuple[str, Any]]:
        """Iterate over all (key, value) pairs."""
        ...

    def clear(self) -> None:
        """Delete all entries."""
        ...


# Import implementations for convenience
from doeff.storage.memory import InMemoryStorage
from doeff.storage.sqlite import SQLiteStorage

__all__ = [
    "DurableStorage",
    "InMemoryStorage",
    "SQLiteStorage",
]
