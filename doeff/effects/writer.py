"""Writer monad effects."""

from __future__ import annotations

from ._program_types import ProgramLike
from .base import Effect, create_effect_with_trace


class writer:
    """Writer monad effects (accumulated logs)."""

    @staticmethod
    def tell(message: object) -> Effect:
        """Add to the log."""
        return create_effect_with_trace("writer.tell", message)

    @staticmethod
    def listen(sub_program: ProgramLike) -> Effect:
        """Run sub-program and return its log.

        Args:
            sub_program: A Program or a thunk that returns a Program
        """
        return create_effect_with_trace("writer.listen", sub_program)


# Uppercase aliases
def Tell(message: object) -> Effect:
    """Writer: Add to the log."""
    return create_effect_with_trace("writer.tell", message, skip_frames=3)


def Listen(sub_program: ProgramLike) -> Effect:
    """Writer: Run sub-program and return its log."""
    return create_effect_with_trace("writer.listen", sub_program, skip_frames=3)


def Log(message: object) -> Effect:
    """Writer: Add to the log (alias for Tell)."""
    return create_effect_with_trace("writer.tell", message, skip_frames=3)


__all__ = [
    "Listen",
    "Log",
    "Tell",
    "writer",
]
