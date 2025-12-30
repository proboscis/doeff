<issue>

# ISSUE-CORE-424: Durable Execution - Storage and Observability APIs

## Summary

Design public APIs for durable workflow execution on top of the CESK interpreter:

1. **Durable Storage API** - `cacheget`/`cacheput` effects with swappable backends (SQLite default)
2. **Execution Observability API** - Monitor K stack and current effect during workflow execution

## Motivation

Long-running workflows need to survive process restarts. Since generators aren't serializable, we use an **effect result caching** approach:

- Cache expensive operation results with persistent keys
- On replay, `cacheget` returns cached results, skipping re-execution
- User ensures idempotency via `cacheget`/`cacheput` pattern

Users also need visibility into workflow execution state for debugging and monitoring.

---

## Design Principles

1. **User-managed idempotency** - User wraps non-idempotent operations with cache checks
2. **Global cache namespace** - Cache is shared across workflows (not scoped per run)
3. **Swappable backends** - Storage backend configurable at interpreter initialization
4. **Live observability** - Users can inspect K stack and current effect during execution

---

## Part A: Durable Storage API

### Effects

```python
# In doeff/effects/cache.py

@dataclass(frozen=True)
class CacheGet:
    """Effect to retrieve a value from durable storage."""
    key: str

@dataclass(frozen=True)
class CachePut:
    """Effect to store a value in durable storage."""
    key: str
    value: Any

@dataclass(frozen=True)
class CacheDelete:
    """Effect to delete a value from durable storage."""
    key: str

@dataclass(frozen=True)
class CacheExists:
    """Effect to check if a key exists in durable storage."""
    key: str

# Convenience functions
def cacheget(key: str) -> CacheGet:
    """Get a value from durable cache."""
    return CacheGet(key)

def cacheput(key: str, value: Any) -> CachePut:
    """Put a value into durable cache."""
    return CachePut(key, value)

def cachedelete(key: str) -> CacheDelete:
    """Delete a value from durable cache."""
    return CacheDelete(key)

def cacheexists(key: str) -> CacheExists:
    """Check if a key exists in durable cache."""
    return CacheExists(key)
```

### Storage Protocol

```python
# In doeff/storage.py

from typing import Protocol, Any, Iterable

class DurableStorage(Protocol):
    """
    Protocol for durable storage backends.

    Implementations must be thread-safe for concurrent access.
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
```

### SQLite Implementation

```python
# In doeff/storage/sqlite.py

import sqlite3
import pickle
import threading
from pathlib import Path

class SQLiteStorage:
    """
    SQLite-backed durable storage.

    Values are serialized using pickle. Thread-safe via connection-per-thread.
    """

    def __init__(self, db_path: str | Path):
        """
        Initialize SQLite storage.

        Args:
            db_path: Path to SQLite database file. Use ":memory:" for in-memory.
        """
        self._db_path = str(db_path)
        self._local = threading.local()
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, 'conn'):
            self._local.conn = sqlite3.connect(self._db_path)
        return self._local.conn

    def _init_schema(self) -> None:
        """Create table if not exists."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                value BLOB NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        conn.commit()

    def get(self, key: str) -> Any | None:
        cursor = self._get_conn().execute(
            "SELECT value FROM cache WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        return pickle.loads(row[0]) if row else None

    def put(self, key: str, value: Any) -> None:
        import time
        now = time.time()
        blob = pickle.dumps(value)
        self._get_conn().execute(
            """
            INSERT INTO cache (key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?
            """,
            (key, blob, now, now, blob, now)
        )
        self._get_conn().commit()

    def delete(self, key: str) -> bool:
        cursor = self._get_conn().execute(
            "DELETE FROM cache WHERE key = ?", (key,)
        )
        self._get_conn().commit()
        return cursor.rowcount > 0

    def exists(self, key: str) -> bool:
        cursor = self._get_conn().execute(
            "SELECT 1 FROM cache WHERE key = ? LIMIT 1", (key,)
        )
        return cursor.fetchone() is not None

    def keys(self) -> Iterable[str]:
        cursor = self._get_conn().execute("SELECT key FROM cache")
        return [row[0] for row in cursor.fetchall()]

    def items(self) -> Iterable[tuple[str, Any]]:
        cursor = self._get_conn().execute("SELECT key, value FROM cache")
        return [(row[0], pickle.loads(row[1])) for row in cursor.fetchall()]

    def clear(self) -> None:
        self._get_conn().execute("DELETE FROM cache")
        self._get_conn().commit()
```

### In-Memory Implementation (for testing)

```python
# In doeff/storage/memory.py

class InMemoryStorage:
    """In-memory storage for testing. Not durable across restarts."""

    def __init__(self):
        self._data: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self._data.get(key)

    def put(self, key: str, value: Any) -> None:
        self._data[key] = value

    def delete(self, key: str) -> bool:
        if key in self._data:
            del self._data[key]
            return True
        return False

    def exists(self, key: str) -> bool:
        return key in self._data

    def keys(self) -> Iterable[str]:
        return list(self._data.keys())

    def items(self) -> Iterable[tuple[str, Any]]:
        return list(self._data.items())

    def clear(self) -> None:
        self._data.clear()
```

### Interpreter Integration

```python
# Storage is passed at interpreter initialization
from doeff.cesk import run_sync
from doeff.storage.sqlite import SQLiteStorage

result = run_sync(
    my_workflow(),
    storage=SQLiteStorage("workflow.db"),  # Optional, default is InMemoryStorage
)
```

### Usage Pattern

```python
from doeff import do
from doeff.effects.cache import cacheget, cacheput

@do
def durable_workflow():
    # Idempotent pattern: check cache before expensive operation
    result = yield cacheget("expensive_step")
    if result is None:
        result = yield perform(expensive_operation())
        yield cacheput("expensive_step", result)

    # Continue with cached or fresh result
    final = yield cacheget("final_step")
    if final is None:
        final = yield perform(process(result))
        yield cacheput("final_step", final)

    return final
```

### Helper: Cached Decorator (Optional Convenience)

```python
@do
def cached(key: str, compute: Callable[[], Generator]) -> Any:
    """
    Helper for idempotent caching pattern.

    Usage:
        result = yield from cached("step1", lambda: perform(expensive_op()))
    """
    result = yield cacheget(key)
    if result is None:
        result = yield from compute()
        yield cacheput(key, result)
    return result
```

---

## Part B: Execution Observability API

### Design

Users need to observe the interpreter's current state:
- What's in the K stack (pending continuations)
- What effect is currently being processed
- Execution status

### Data Types

```python
# In doeff/cesk_observability.py

from dataclasses import dataclass
from typing import Any, Literal
from doeff.cesk_traceback import CodeLocation

ExecutionStatus = Literal["pending", "running", "paused", "completed", "failed"]

@dataclass(frozen=True)
class KFrameSnapshot:
    """Snapshot of a single K frame for observability."""
    frame_type: str  # "ReturnFrame", "CatchFrame", "FinallyFrame", etc.
    location: CodeLocation | None  # Where this frame was created
    description: str  # Human-readable description

@dataclass(frozen=True)
class ExecutionSnapshot:
    """Point-in-time snapshot of execution state."""
    status: ExecutionStatus
    k_stack: tuple[KFrameSnapshot, ...]  # Current continuation stack
    current_effect: Any | None  # Effect being processed (if any)
    step_count: int  # Number of steps executed
    cache_keys: tuple[str, ...]  # Keys in durable storage
```

### Execution Monitor

```python
@dataclass
class ExecutionMonitor:
    """
    Live monitor for workflow execution.

    Provides read-only access to interpreter state.
    Thread-safe for external observation.
    """

    def snapshot(self) -> ExecutionSnapshot:
        """Get current execution state snapshot."""
        ...

    @property
    def status(self) -> ExecutionStatus:
        """Current execution status."""
        ...

    @property
    def k_stack(self) -> tuple[KFrameSnapshot, ...]:
        """Current K stack (pending continuations)."""
        ...

    @property
    def current_effect(self) -> Any | None:
        """Effect currently being processed, or None."""
        ...

    @property
    def step_count(self) -> int:
        """Number of interpreter steps executed."""
        ...

    def get_cache_entries(self) -> dict[str, Any]:
        """Get all cache entries (via storage)."""
        ...
```

### Integration with run_sync

```python
# Option 1: Callback-based observation
def run_sync(
    program: KleisliProgram[T],
    storage: DurableStorage | None = None,
    on_step: Callable[[ExecutionSnapshot], None] | None = None,
) -> CESKResult[T]:
    ...

# Option 2: Return monitor handle (for async/threaded contexts)
@dataclass
class WorkflowExecution(Generic[T]):
    """Handle for observing and awaiting workflow execution."""
    monitor: ExecutionMonitor
    result: CESKResult[T] | None  # None while running

    def wait(self) -> CESKResult[T]:
        """Block until execution completes."""
        ...

def run_workflow(
    program: KleisliProgram[T],
    storage: DurableStorage | None = None,
) -> WorkflowExecution[T]:
    ...
```

### Usage: Callback-based

```python
def log_step(snapshot: ExecutionSnapshot):
    print(f"Step {snapshot.step_count}: {snapshot.status}")
    if snapshot.current_effect:
        print(f"  Processing: {snapshot.current_effect}")
    print(f"  K depth: {len(snapshot.k_stack)}")

result = run_sync(
    my_workflow(),
    storage=SQLiteStorage("workflow.db"),
    on_step=log_step,
)
```

### Usage: Monitor Handle (async context)

```python
import asyncio

async def run_with_monitoring():
    execution = run_workflow(
        my_workflow(),
        storage=SQLiteStorage("workflow.db"),
    )

    # Monitor in background while executing
    while execution.result is None:
        snapshot = execution.monitor.snapshot()
        print(f"Status: {snapshot.status}, K depth: {len(snapshot.k_stack)}")
        await asyncio.sleep(0.1)

    return execution.result
```

---

## Public API Summary

| Type/Function | Visibility | Location |
|---------------|------------|----------|
| `CacheGet`, `CachePut`, `CacheDelete`, `CacheExists` | **Public** | `doeff.effects.cache` |
| `cacheget()`, `cacheput()`, `cachedelete()`, `cacheexists()` | **Public** | `doeff.effects.cache` |
| `DurableStorage` | **Public** | `doeff.storage` |
| `SQLiteStorage` | **Public** | `doeff.storage.sqlite` |
| `InMemoryStorage` | **Public** | `doeff.storage.memory` |
| `ExecutionSnapshot` | **Public** | `doeff.cesk_observability` |
| `ExecutionMonitor` | **Public** | `doeff.cesk_observability` |
| `KFrameSnapshot` | **Public** | `doeff.cesk_observability` |

---

## Interpreter Changes

### run_sync signature update

```python
def run_sync(
    program: KleisliProgram[T],
    *,
    storage: DurableStorage | None = None,
    on_step: Callable[[ExecutionSnapshot], None] | None = None,
) -> CESKResult[T]:
    """
    Run program synchronously with optional durable storage and observability.

    Args:
        program: The Kleisli program to execute
        storage: Durable storage backend for cache effects (default: InMemoryStorage)
        on_step: Optional callback invoked after each interpreter step

    Returns:
        CESKResult containing the result and any captured traceback
    """
```

### Effect Handler for Cache Effects

```python
# In interpreter, handle cache effects:
def handle_cache_effect(effect: Any, storage: DurableStorage) -> Any:
    match effect:
        case CacheGet(key):
            return storage.get(key)
        case CachePut(key, value):
            storage.put(key, value)
            return None
        case CacheDelete(key):
            return storage.delete(key)
        case CacheExists(key):
            return storage.exists(key)
        case _:
            raise UnhandledEffect(effect)
```

---

## Test Cases

| Test ID | Scenario | Expected |
|---------|----------|----------|
| T1 | `cacheput` then `cacheget` same key | Returns cached value |
| T2 | `cacheget` non-existent key | Returns None |
| T3 | `cacheput` overwrites existing | New value returned |
| T4 | `cachedelete` existing key | Returns True, key gone |
| T5 | `cacheexists` existing/non-existing | True/False |
| T6 | SQLite persists across connections | Value survives reconnect |
| T7 | Workflow replay uses cached values | Expensive op not re-run |
| T8 | `on_step` callback invoked | Called each step |
| T9 | `ExecutionSnapshot.k_stack` accurate | Matches actual K |
| T10 | `ExecutionMonitor` thread-safe | No races under concurrent access |

---

## Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC1 | `cacheget`/`cacheput` effects work with SQLite backend |
| AC2 | Storage backend swappable at `run_sync()` call |
| AC3 | `on_step` callback receives accurate `ExecutionSnapshot` |
| AC4 | `K stack` in snapshot reflects actual continuation stack |
| AC5 | SQLite storage persists across process restarts |
| AC6 | InMemoryStorage works for testing |
| AC7 | Thread-safe access to ExecutionMonitor |

---

## Future Work (Out of Scope)

1. **Distributed storage backends** - Redis, PostgreSQL, etc.
2. **Workflow versioning** - Handle code changes between restarts
3. **Automatic checkpointing** - Periodic snapshots without explicit cacheput
4. **Workflow replay/debugging** - Step through cached execution
5. **TTL/expiration** - Cache entry expiration policies
6. **Multi-process coordination** - Locking, leader election

---

## Open Questions

1. **Cache key namespacing** - Should we provide helpers for structured keys (e.g., `f"{workflow_name}:{step_id}"`)?
2. **Serialization** - Pickle is default; should we support other formats (JSON, msgpack)?
3. **Error handling** - What if storage fails during cacheput? Retry? Fail workflow?

</issue>

Instructions:
- Implement the changes described in the issue above
- Run tests to verify your changes work correctly
- When complete, create a pull request targeting `main`:
  - Title should summarize the change
  - Body should reference issue: ISSUE-CORE-424
  - Include a summary of changes made
