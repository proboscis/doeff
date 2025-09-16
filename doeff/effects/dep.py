"""
Dependency injection effects.

This module provides dependency injection effects compatible with pinjected.
"""

from .base import Effect


class dep:
    """Dependency injection (pinjected compatible)."""

    @staticmethod
    def inject(key: str) -> Effect:
        """Request dependency injection."""
        return Effect("dep.inject", key)


# Uppercase aliases
def Dep(key: str) -> Effect:
    """Dependency: Request dependency injection."""
    return dep.inject(key)


# No lowercase alias for Dep to avoid confusion


__all__ = [
    "dep",
    "Dep",
]