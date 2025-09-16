"""
Future/async effects.

This module provides Future effects for async operations.
"""

from typing import Any, Awaitable

from .base import Effect


class future:
    """Future/async effects (forced evaluation)."""

    @staticmethod
    def await_(awaitable: Awaitable[Any]) -> Effect:
        """Await an async operation."""
        return Effect("future.await", awaitable)

    @staticmethod
    def parallel(*awaitables: Awaitable[Any]) -> Effect:
        """Run multiple async operations in parallel."""
        return Effect("future.parallel", awaitables)


# Uppercase aliases
def Await(awaitable: Awaitable[Any]) -> Effect:
    """Future: Await an async operation."""
    return future.await_(awaitable)


def Parallel(*awaitables: Awaitable[Any]) -> Effect:
    """Future: Run multiple async operations in parallel."""
    return future.parallel(*awaitables)


__all__ = [
    "future",
    "Await",
    "Parallel",
]