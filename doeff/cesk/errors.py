"""CESK machine error types."""

from __future__ import annotations


class HandlerRegistryError(Exception):
    """Raised when there's a conflict or invalid handler registration."""


class UnhandledEffectError(Exception):
    """Raised when no handler exists for an effect."""


class InterpreterInvariantError(Exception):
    """Raised when the interpreter reaches an invalid state."""


__all__ = [
    "HandlerRegistryError",
    "UnhandledEffectError",
    "InterpreterInvariantError",
]
