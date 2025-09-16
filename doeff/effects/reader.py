"""
Reader monad effects.

This module provides Reader effects for environment-based configuration.
"""

from typing import Any, Dict

from .base import Effect


class reader:
    """Reader monad effects (forced evaluation)."""

    @staticmethod
    def ask(key: str) -> Effect:
        """Ask for environment value."""
        return Effect("reader.ask", key)

    @staticmethod
    def local(env_update: Dict[str, Any], sub_program: Any) -> Effect:
        """Run sub-program with modified environment.

        Args:
            env_update: Environment updates to apply
            sub_program: A Program or a thunk that returns a Program
        """
        return Effect("reader.local", {"env": env_update, "program": sub_program})


# Uppercase aliases
def Ask(key: str) -> Effect:
    """Reader: Ask for environment value."""
    return reader.ask(key)


def Local(env_update: Dict[str, Any], sub_program: Any) -> Effect:
    """Reader: Run sub-program with modified environment."""
    return reader.local(env_update, sub_program)


__all__ = [
    "reader",
    "Ask",
    "Local",
]