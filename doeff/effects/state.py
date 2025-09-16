"""
State monad effects.

This module provides State effects for managing mutable state.
"""

from typing import Any, Callable

from .base import Effect


class state:
    """State monad effects (threaded through computation)."""

    @staticmethod
    def get(key: str) -> Effect:
        """Get value from state."""
        return Effect("state.get", key)

    @staticmethod
    def put(key: str, value: Any) -> Effect:
        """Update state value."""
        return Effect("state.put", {"key": key, "value": value})

    @staticmethod
    def modify(key: str, f: Callable[[Any], Any]) -> Effect:
        """Modify state value with function."""
        return Effect("state.modify", {"key": key, "func": f})


# Uppercase aliases
def Get(key: str) -> Effect:
    """State: Get value from state."""
    return state.get(key)


def Put(key: str, value: Any) -> Effect:
    """State: Update state value."""
    return state.put(key, value)


def Modify(key: str, f: Callable[[Any], Any]) -> Effect:
    """State: Modify state value with function."""
    return state.modify(key, f)


__all__ = [
    "state",
    "Get",
    "Put",
    "Modify",
]