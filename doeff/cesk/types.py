"""
CESK Machine type aliases and basic types.

This module contains core types for the unified multi-task CESK architecture:

- TaskId: Unique identifier for tasks
- FutureId: Unique identifier for futures
- SpawnId: Unique identifier for spawn operations
- Environment: Immutable mapping (copy-on-write semantics)
- Store: Mutable state dict
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NewType, TypeAlias

from doeff._vendor import FrozenDict


# Task identifiers - unique identifiers for tracking tasks and operations
TaskId = NewType("TaskId", int)
FutureId = NewType("FutureId", int)
SpawnId = NewType("SpawnId", int)


# E: Environment - immutable mapping (copy-on-write semantics)
# Used for reader context that can be locally scoped
Environment: TypeAlias = FrozenDict[Any, Any]


# S: Store - mutable state dict
# Reserved keys: __log__, __memo__, __cache_storage__, __dispatcher__, __current_time__
# Each task has its own view of the store, but all tasks share the same store instance
Store: TypeAlias = dict[str, Any]


@dataclass(frozen=True)
class TaskIdGenerator:
    """Generator for unique task IDs."""
    
    _counter: int = 0
    
    def next(self) -> tuple[TaskId, "TaskIdGenerator"]:
        """Generate next unique TaskId."""
        new_gen = TaskIdGenerator(self._counter + 1)
        return TaskId(self._counter), new_gen


@dataclass(frozen=True)
class FutureIdGenerator:
    """Generator for unique future IDs."""
    
    _counter: int = 0
    
    def next(self) -> tuple[FutureId, "FutureIdGenerator"]:
        """Generate next unique FutureId."""
        new_gen = FutureIdGenerator(self._counter + 1)
        return FutureId(self._counter), new_gen


@dataclass(frozen=True)
class SpawnIdGenerator:
    """Generator for unique spawn IDs."""
    
    _counter: int = 0
    
    def next(self) -> tuple[SpawnId, "SpawnIdGenerator"]:
        """Generate next unique SpawnId."""
        new_gen = SpawnIdGenerator(self._counter + 1)
        return SpawnId(self._counter), new_gen


__all__ = [
    "TaskId",
    "FutureId",
    "SpawnId",
    "Environment",
    "Store",
    "TaskIdGenerator",
    "FutureIdGenerator",
    "SpawnIdGenerator",
]
