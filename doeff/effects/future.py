"""
Future/async effects.

This module provides Future effects for async operations.
"""

from typing import Any, Awaitable

from .base import Effect, create_effect_with_trace


class future:
    """Future/async effects (forced evaluation)."""

    @staticmethod
    def await_(awaitable: Awaitable[Any]) -> Effect:
        """Await an async operation."""
        return create_effect_with_trace("future.await", awaitable)

    @staticmethod
    def parallel(*awaitables: Awaitable[Any]) -> Effect:
        """Run multiple async operations in parallel."""
        return create_effect_with_trace("future.parallel", awaitables)


# Uppercase aliases
def Await(awaitable: Awaitable[Any]) -> Effect:
    """Future: Await an async operation."""
    return create_effect_with_trace("future.await", awaitable, skip_frames=3)


def Parallel(*awaitables: Awaitable[Any]) -> Effect:
    """Future: Run multiple async operations in parallel."""
    return create_effect_with_trace("future.parallel", awaitables, skip_frames=3)


__all__ = [
    "future",
    "Await",
    "Parallel",
]