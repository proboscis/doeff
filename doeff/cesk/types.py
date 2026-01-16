"""
CESK Machine type aliases and basic types.

This module contains:
- TaskId, FutureId, SpawnId: Type-safe identifiers for multi-task CESK
- Environment: Immutable mapping (copy-on-write semantics)
- Store: Mutable state dict
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NewType, TypeAlias

from doeff._vendor import FrozenDict


# ============================================================================
# Type-safe Identifiers for Multi-task CESK
# ============================================================================


@dataclass(frozen=True, slots=True)
class TaskId:
    """Unique identifier for a task in the CESK machine.

    Tasks are independent units of computation that can be scheduled,
    suspended, and resumed. Each task has its own Control and Kontinuation.
    """

    value: int

    def __str__(self) -> str:
        return f"task-{self.value}"


@dataclass(frozen=True, slots=True)
class FutureId:
    """Unique identifier for a future (pending result) in the CESK machine.

    Futures represent values that will be available when a task completes.
    Other tasks can wait on futures using join/gather/race operations.
    """

    value: int

    def __str__(self) -> str:
        return f"future-{self.value}"


@dataclass(frozen=True, slots=True)
class SpawnId:
    """Unique identifier for a spawn operation.

    SpawnId links a spawn request to the resulting TaskId and FutureId.
    Used internally to track task creation during step().
    """

    value: int

    def __str__(self) -> str:
        return f"spawn-{self.value}"


# ============================================================================
# Core CESK Types
# ============================================================================


# E: Environment - immutable mapping (copy-on-write semantics)
Environment: TypeAlias = FrozenDict[Any, Any]

# S: Store - mutable state (dict with reserved keys: __log__, __memo__, __cache_storage__, __dispatcher__)
Store: TypeAlias = dict[str, Any]


__all__ = [
    # Identifiers
    "TaskId",
    "FutureId",
    "SpawnId",
    # Core types
    "Environment",
    "Store",
]
