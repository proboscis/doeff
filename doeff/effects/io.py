"""
IO effects.

This module provides IO effects for performing side effects.
"""

from typing import Any, Callable

from .base import Effect, create_effect_with_trace


class io:
    """IO effects (executed immediately, not deferred)."""

    @staticmethod
    def perform(action: Callable[[], Any]) -> Effect:
        """Perform an IO action."""
        return create_effect_with_trace("io.perform", action)
    
    @staticmethod
    def run(action: Callable[[], Any]) -> Effect:
        """Perform an IO action (alias for perform)."""
        return create_effect_with_trace("io.run", action)

    @staticmethod
    def print(message: str) -> Effect:
        """Print to stdout."""
        return create_effect_with_trace("io.print", message)


# Uppercase aliases
def IO(action: Callable[[], Any]) -> Effect:
    """IO: Perform an IO action."""
    return create_effect_with_trace("io.perform", action, skip_frames=3)


def Print(message: str) -> Effect:
    """IO: Print to stdout."""
    return create_effect_with_trace("io.print", message, skip_frames=3)


__all__ = [
    "io",
    "IO",
    "Print",
]