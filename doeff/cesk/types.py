"""
CESK Machine type aliases and basic types.

This module contains:
- Environment: Immutable mapping (copy-on-write semantics)
- Store: Mutable state dict
"""

from __future__ import annotations

from typing import Any, TypeAlias

from doeff._vendor import FrozenDict


# E: Environment - immutable mapping (copy-on-write semantics)
Environment: TypeAlias = FrozenDict[Any, Any]

# S: Store - mutable state (dict with reserved keys: __log__, __memo__, __durable_storage__, __dispatcher__)
Store: TypeAlias = dict[str, Any]


__all__ = [
    "Environment",
    "Store",
]
