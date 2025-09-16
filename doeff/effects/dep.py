"""
Dependency injection effects.

This module provides dependency injection effects compatible with pinjected.
"""

from .base import Effect, create_effect_with_trace


class dep:
    """Dependency injection (pinjected compatible)."""

    @staticmethod
    def inject(key: str) -> Effect:
        """Request dependency injection."""
        return create_effect_with_trace("dep.inject", key)


# Uppercase aliases
def Dep(key: str) -> Effect:
    """Dependency: Request dependency injection."""
    return create_effect_with_trace("dep.inject", key, skip_frames=3)


# No lowercase alias for Dep to avoid confusion


__all__ = [
    "dep",
    "Dep",
]