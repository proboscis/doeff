"""
Reader monad effects.

This module provides Reader effects for environment-based configuration.
"""

from typing import Any

from .base import Effect, create_effect_with_trace


class reader:
    """Reader monad effects (forced evaluation)."""

    @staticmethod
    def ask(key: str) -> Effect:
        """Ask for environment value."""
        return create_effect_with_trace("reader.ask", key)

    @staticmethod
    def local(env_update: dict[str, Any], sub_program: Any) -> Effect:
        """Run sub-program with modified environment.

        Args:
            env_update: Environment updates to apply
            sub_program: A Program or a thunk that returns a Program
        """
        return create_effect_with_trace("reader.local", {"env": env_update, "program": sub_program})


# Uppercase aliases
def Ask(key: str) -> Effect:
    """Reader: Ask for environment value."""
    return create_effect_with_trace("reader.ask", key, skip_frames=3)


def Local(env_update: dict[str, Any], sub_program: Any) -> Effect:
    """Reader: Run sub-program with modified environment."""
    return create_effect_with_trace("reader.local", {"env": env_update, "program": sub_program}, skip_frames=3)


__all__ = [
    "Ask",
    "Local",
    "reader",
]
