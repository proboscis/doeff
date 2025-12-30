"""
Durable cache effects for persistent workflow execution.

These effects provide simple key-value storage for durable workflows,
allowing expensive operation results to be cached and retrieved across
process restarts.

Usage:
    @do
    def durable_workflow():
        # Idempotent pattern: check cache before expensive operation
        result = yield cacheget("expensive_step")
        if result is None:
            result = yield perform(expensive_operation())
            yield cacheput("expensive_step", result)
        return result

Public API:
    - DurableCacheGet: Effect to retrieve a value from durable storage
    - DurableCachePut: Effect to store a value in durable storage
    - DurableCacheDelete: Effect to delete a value from durable storage
    - DurableCacheExists: Effect to check if a key exists in durable storage
    - cacheget, cacheput, cachedelete, cacheexists: Convenience functions
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from .base import EffectBase, create_effect_with_trace

if TYPE_CHECKING:
    from doeff.program import Program
    from doeff.types import Effect


@dataclass(frozen=True)
class DurableCacheGet(EffectBase):
    """
    Effect to retrieve a value from durable storage.

    Returns the stored value, or None if the key does not exist.

    Attributes:
        key: The cache key to retrieve.
    """

    key: str

    def intercept(
        self, transform: Callable[["Effect"], "Effect | Program"]
    ) -> "DurableCacheGet":
        return self


@dataclass(frozen=True)
class DurableCachePut(EffectBase):
    """
    Effect to store a value in durable storage.

    Overwrites any existing value with the same key.
    Returns None after successful storage.

    Attributes:
        key: The cache key to store under.
        value: The value to store (must be picklable for SQLite backend).
    """

    key: str
    value: Any

    def intercept(
        self, transform: Callable[["Effect"], "Effect | Program"]
    ) -> "DurableCachePut":
        return self


@dataclass(frozen=True)
class DurableCacheDelete(EffectBase):
    """
    Effect to delete a value from durable storage.

    Returns True if the key existed and was deleted, False otherwise.

    Attributes:
        key: The cache key to delete.
    """

    key: str

    def intercept(
        self, transform: Callable[["Effect"], "Effect | Program"]
    ) -> "DurableCacheDelete":
        return self


@dataclass(frozen=True)
class DurableCacheExists(EffectBase):
    """
    Effect to check if a key exists in durable storage.

    Returns True if the key exists, False otherwise.

    Attributes:
        key: The cache key to check.
    """

    key: str

    def intercept(
        self, transform: Callable[["Effect"], "Effect | Program"]
    ) -> "DurableCacheExists":
        return self


# ============================================================================
# Convenience Functions (PascalCase variants for consistency with other effects)
# ============================================================================


def cacheget(key: str) -> DurableCacheGet:
    """
    Get a value from durable cache.

    Args:
        key: The cache key to retrieve.

    Returns:
        DurableCacheGet effect that yields the cached value or None.

    Example:
        result = yield cacheget("my_step_result")
        if result is None:
            # Not cached, compute it
            ...
    """
    return create_effect_with_trace(DurableCacheGet(key=key))


def cacheput(key: str, value: Any) -> DurableCachePut:
    """
    Put a value into durable cache.

    Args:
        key: The cache key to store under.
        value: The value to store.

    Returns:
        DurableCachePut effect that stores the value.

    Example:
        yield cacheput("my_step_result", computed_value)
    """
    return create_effect_with_trace(DurableCachePut(key=key, value=value))


def cachedelete(key: str) -> DurableCacheDelete:
    """
    Delete a value from durable cache.

    Args:
        key: The cache key to delete.

    Returns:
        DurableCacheDelete effect that yields True if deleted, False otherwise.

    Example:
        was_deleted = yield cachedelete("old_step_result")
    """
    return create_effect_with_trace(DurableCacheDelete(key=key))


def cacheexists(key: str) -> DurableCacheExists:
    """
    Check if a key exists in durable cache.

    Args:
        key: The cache key to check.

    Returns:
        DurableCacheExists effect that yields True if exists, False otherwise.

    Example:
        if (yield cacheexists("my_step_result")):
            result = yield cacheget("my_step_result")
    """
    return create_effect_with_trace(DurableCacheExists(key=key))


__all__ = [
    # Effect types
    "DurableCacheGet",
    "DurableCachePut",
    "DurableCacheDelete",
    "DurableCacheExists",
    # Convenience functions
    "cacheget",
    "cacheput",
    "cachedelete",
    "cacheexists",
]
