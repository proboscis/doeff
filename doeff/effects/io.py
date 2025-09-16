"""
IO effects.

This module provides IO effects for performing side effects.
"""

from typing import Any, Callable

from .base import Effect


class io:
    """IO effects (executed immediately, not deferred)."""

    @staticmethod
    def perform(action: Callable[[], Any]) -> Effect:
        """Perform an IO action."""
        return Effect("io.perform", action)
    
    @staticmethod
    def run(action: Callable[[], Any]) -> Effect:
        """Perform an IO action (alias for perform)."""
        return Effect("io.run", action)

    @staticmethod
    def print(message: str) -> Effect:
        """Print to stdout."""
        return Effect("io.print", message)


# Uppercase aliases
def IO(action: Callable[[], Any]) -> Effect:
    """IO: Perform an IO action."""
    return io.perform(action)


def Print(message: str) -> Effect:
    """IO: Print to stdout."""
    return io.print(message)


__all__ = [
    "io",
    "IO",
    "Print",
]