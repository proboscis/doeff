"""
Writer monad effects.

This module provides Writer effects for logging and accumulating output.
"""

from typing import Any

from .base import Effect, create_effect_with_trace


class writer:
    """Writer monad effects (accumulated logs)."""

    @staticmethod
    def tell(message: Any) -> Effect:
        """Add to the log."""
        return create_effect_with_trace("writer.tell", message)

    @staticmethod
    def listen(sub_program: Any) -> Effect:
        """Run sub-program and return its log.

        Args:
            sub_program: A Program or a thunk that returns a Program
        """
        return create_effect_with_trace("writer.listen", sub_program)


# Uppercase aliases
def Tell(message: Any) -> Effect:
    """Writer: Add to the log."""
    return create_effect_with_trace("writer.tell", message, skip_frames=3)


def Listen(sub_program: Any) -> Effect:
    """Writer: Run sub-program and return its log."""
    return create_effect_with_trace("writer.listen", sub_program, skip_frames=3)


def Log(message: Any) -> Effect:
    """Writer: Add to the log (alias for Tell)."""
    return create_effect_with_trace("writer.tell", message, skip_frames=3)


__all__ = [
    "Listen",
    "Log",
    "Tell",
    "writer",
]
