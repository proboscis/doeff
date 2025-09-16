"""
Graph tracking effects.

This module provides Graph effects for tracking computation steps.
"""

from typing import Any, Dict

from .base import Effect


class graph:
    """Graph tracking effects."""

    @staticmethod
    def step(value: Any, meta: Dict[str, Any] | None = None) -> Effect:
        """Track a computation step."""
        return Effect("graph.step", {"value": value, "meta": meta or {}})

    @staticmethod
    def annotate(meta: Dict[str, Any]) -> Effect:
        """Annotate the current step."""
        return Effect("graph.annotate", meta)


# Uppercase aliases
def Step(value: Any, meta: Dict[str, Any] | None = None) -> Effect:
    """Graph: Track a computation step."""
    return graph.step(value, meta)


def Annotate(meta: Dict[str, Any]) -> Effect:
    """Graph: Annotate the current step."""
    return graph.annotate(meta)


__all__ = [
    "graph",
    "Step",
    "Annotate",
]