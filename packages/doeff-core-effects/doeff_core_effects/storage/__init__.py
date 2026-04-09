"""
Durable storage interfaces and implementations for doeff workflows.

This module provides the storage abstraction layer for durable workflow execution.
The storage is used by memo effects (MemoGet/MemoPut) to persist memoized results
across process restarts.

Public API:
- DurableStorage: Protocol for storage backends
- InMemoryStorage: In-memory storage (for testing, not durable)
- SQLiteStorage: SQLite-backed persistent storage

Example usage:
    from doeff.storage import SQLiteStorage
    from doeff import run

    result = run(
        my_workflow(),
        storage=SQLiteStorage("workflow.db"),
    )
"""


from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DurableStorage(Protocol):
    """
    Protocol for effectful durable storage backends.

    All methods return Program[T] — the cache handler yields them.
    This makes storage I/O composable with doeff's effect system:
    - Local storage (SQLite, memory): return Pure(value)
    - Network storage (Redis): return Await(asyncio.to_thread(...))

    Implementations must be thread-safe for concurrent access.
    Values are opaque - the storage layer handles serialization.
    """

    def get(self, key: str) -> Any:
        """Get value by key. Returns Program[Any | None]."""
        ...

    def put(self, key: str, value: Any) -> Any:
        """Store value with key. Returns Program[None]."""
        ...

    def delete(self, key: str) -> Any:
        """Delete key. Returns Program[bool]."""
        ...

    def exists(self, key: str) -> Any:
        """Check if key exists. Returns Program[bool]."""
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
from doeff_core_effects.storage.memory import InMemoryStorage
from doeff_core_effects.storage.sqlite import SQLiteStorage

__all__ = [
    "DurableStorage",
    "InMemoryStorage",
    "SQLiteStorage",
    "is_program",
]
