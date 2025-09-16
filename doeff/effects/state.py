"""
State monad effects.

This module provides State effects for managing mutable state.
"""

from collections.abc import Callable
from typing import Any

from .base import Effect, create_effect_with_trace


class state:
    """State monad effects (threaded through computation)."""

    @staticmethod
    def get(key: str) -> Effect:
        """Get value from state."""
        return create_effect_with_trace("state.get", key)

    @staticmethod
    def put(key: str, value: Any) -> Effect:
        """Update state value."""
        return create_effect_with_trace("state.put", {"key": key, "value": value})

    @staticmethod
    def modify(key: str, f: Callable[[Any], Any]) -> Effect:
        """Modify state value with function."""
        return create_effect_with_trace("state.modify", {"key": key, "func": f})


# Uppercase aliases
def Get(key: str) -> Effect:
    """State: Get value from state."""
    return create_effect_with_trace("state.get", key, skip_frames=3)


def Put(key: str, value: Any) -> Effect:
    """State: Update state value."""
    return create_effect_with_trace("state.put", {"key": key, "value": value}, skip_frames=3)


def Modify(key: str, f: Callable[[Any], Any]) -> Effect:
    """State: Modify state value with function."""
    return create_effect_with_trace("state.modify", {"key": key, "func": f}, skip_frames=3)


__all__ = [
    "Get",
    "Modify",
    "Put",
    "state",
]
