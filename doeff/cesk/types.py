"""
Unified CESK Machine type aliases and identity types.

This module contains:
- TaskId, FutureId, SpawnId: Identity types for multi-task coordination
- Environment: Immutable mapping (copy-on-write semantics)
- Store: Mutable state dict
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NewType, TypeAlias

from doeff._vendor import FrozenDict


# Identity types for multi-task CESK
TaskId = NewType("TaskId", int)
FutureId = NewType("FutureId", int)
SpawnId = NewType("SpawnId", int)


@dataclass(frozen=True)
class TaskHandle:
    """Handle to a spawned task, allowing join operations."""
    
    task_id: TaskId
    
    def __repr__(self) -> str:
        return f"TaskHandle({self.task_id})"


@dataclass(frozen=True)
class FutureHandle:
    """Handle to a future, allowing await and resolve operations."""
    
    future_id: FutureId
    
    def __repr__(self) -> str:
        return f"FutureHandle({self.future_id})"


@dataclass(frozen=True)
class SpawnHandle:
    """Handle to an external spawn (e.g., thread, process), allowing join."""
    
    spawn_id: SpawnId
    
    def __repr__(self) -> str:
        return f"SpawnHandle({self.spawn_id})"


# E: Environment - immutable mapping (copy-on-write semantics)
Environment: TypeAlias = FrozenDict[Any, Any]

# S: Store - mutable state (dict with reserved keys: __log__, __memo__, __cache_storage__, __dispatcher__)
Store: TypeAlias = dict[str, Any]


__all__ = [
    "TaskId",
    "FutureId",
    "SpawnId",
    "TaskHandle",
    "FutureHandle",
    "SpawnHandle",
    "Environment",
    "Store",
]
