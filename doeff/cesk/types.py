"""
CESK Machine type aliases and basic types for unified multi-task architecture.

This module contains:
- TaskId: Unique identifier for tasks
- FutureId: Unique identifier for futures
- SpawnId: Unique identifier for spawned processes
- Environment: Immutable mapping (copy-on-write semantics)
- Store: Mutable state dict
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NewType, TypeAlias
from uuid import UUID, uuid4

from doeff._vendor import FrozenDict


# ============================================
# Identity Types
# ============================================

@dataclass(frozen=True, order=True)
class TaskId:
    """Unique identifier for a task in the CESK runtime.

    Tasks are the unit of concurrent execution in the unified CESK architecture.
    Each task has its own continuation stack (K) but shares the global Store (S).
    """

    _id: UUID = field(default_factory=uuid4, compare=True)

    @classmethod
    def new(cls) -> TaskId:
        """Create a new unique TaskId."""
        return cls()

    def __str__(self) -> str:
        return f"task-{self._id.hex[:8]}"

    def __repr__(self) -> str:
        return f"TaskId({self._id.hex[:8]})"


@dataclass(frozen=True, order=True)
class FutureId:
    """Unique identifier for a future/promise.

    Futures represent values that will be available in the future.
    Tasks can wait on futures via FutureCondition.
    """

    _id: UUID = field(default_factory=uuid4, compare=True)

    @classmethod
    def new(cls) -> FutureId:
        """Create a new unique FutureId."""
        return cls()

    def __str__(self) -> str:
        return f"future-{self._id.hex[:8]}"

    def __repr__(self) -> str:
        return f"FutureId({self._id.hex[:8]})"


@dataclass(frozen=True, order=True)
class SpawnId:
    """Unique identifier for a spawned subprocess/external process.

    SpawnId is used for processes spawned via external backends (thread pool,
    process pool, etc.) as opposed to in-process tasks.
    """

    _id: UUID = field(default_factory=uuid4, compare=True)

    @classmethod
    def new(cls) -> SpawnId:
        """Create a new unique SpawnId."""
        return cls()

    def __str__(self) -> str:
        return f"spawn-{self._id.hex[:8]}"

    def __repr__(self) -> str:
        return f"SpawnId({self._id.hex[:8]})"


# ============================================
# Task Handle
# ============================================

@dataclass(frozen=True)
class TaskHandle:
    """Handle returned when a task is created.

    This handle can be used with join() or gather() to wait for task completion.
    """

    task_id: TaskId

    def __str__(self) -> str:
        return f"TaskHandle({self.task_id})"


@dataclass(frozen=True)
class FutureHandle:
    """Handle returned when a future is created.

    This handle can be used to wait on or resolve the future.
    """

    future_id: FutureId

    def __str__(self) -> str:
        return f"FutureHandle({self.future_id})"


@dataclass(frozen=True)
class SpawnHandle:
    """Handle returned when a subprocess is spawned.

    This handle can be used to wait for the spawned process to complete.
    """

    spawn_id: SpawnId

    def __str__(self) -> str:
        return f"SpawnHandle({self.spawn_id})"


# ============================================
# Core CESK Types
# ============================================

# E: Environment - immutable mapping (copy-on-write semantics)
Environment: TypeAlias = FrozenDict[Any, Any]

# S: Store - mutable state dict
# Reserved keys: __log__, __memo__, __cache_storage__, __dispatcher__, __current_time__
Store: TypeAlias = dict[str, Any]


def empty_environment() -> Environment:
    """Create an empty Environment."""
    return FrozenDict()


def empty_store() -> Store:
    """Create an empty Store."""
    return {}


__all__ = [
    # Identity types
    "TaskId",
    "FutureId",
    "SpawnId",
    # Handle types
    "TaskHandle",
    "FutureHandle",
    "SpawnHandle",
    # Core types
    "Environment",
    "Store",
    # Factory functions
    "empty_environment",
    "empty_store",
]
