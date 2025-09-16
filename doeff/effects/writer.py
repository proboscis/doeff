"""
Writer monad effects.

This module provides Writer effects for logging and accumulating output.
"""

from typing import Any

from .base import Effect


class writer:
    """Writer monad effects (accumulated logs)."""

    @staticmethod
    def tell(message: Any) -> Effect:
        """Add to the log."""
        return Effect("writer.tell", message)

    @staticmethod
    def listen(sub_program: Any) -> Effect:
        """Run sub-program and return its log.

        Args:
            sub_program: A Program or a thunk that returns a Program
        """
        return Effect("writer.listen", sub_program)


# Uppercase aliases
def Tell(message: Any) -> Effect:
    """Writer: Add to the log."""
    return writer.tell(message)


def Listen(sub_program: Any) -> Effect:
    """Writer: Run sub-program and return its log."""
    return writer.listen(sub_program)


def Log(message: Any) -> Effect:
    """Writer: Add to the log (alias for Tell)."""
    return writer.tell(message)


__all__ = [
    "writer",
    "Tell",
    "Listen",
    "Log",
]