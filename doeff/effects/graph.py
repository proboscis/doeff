"""
Graph tracking effects.

This module provides Graph effects for tracking computation steps.
"""

from typing import Any

from .base import Effect, create_effect_with_trace


class graph:
    """Graph tracking effects."""

    @staticmethod
    def step(value: Any, meta: dict[str, Any] | None = None) -> Effect:
        """Track a computation step."""
        return create_effect_with_trace("graph.step", {"value": value, "meta": meta or {}})

    @staticmethod
    def annotate(meta: dict[str, Any]) -> Effect:
        """Annotate the current step."""
        return create_effect_with_trace("graph.annotate", meta)

    @staticmethod
    def snapshot() -> Effect:
        """Fetch the current computation graph."""
        return create_effect_with_trace("graph.snapshot", None)


# Uppercase aliases
def Step(value: Any, meta: dict[str, Any] | None = None) -> Effect:
    """Graph: Track a computation step."""
    return create_effect_with_trace("graph.step", {"value": value, "meta": meta or {}}, skip_frames=3)


def Annotate(meta: dict[str, Any]) -> Effect:
    """Graph: Annotate the current step."""
    return create_effect_with_trace("graph.annotate", meta, skip_frames=3)


def Snapshot() -> Effect:
    """Graph: Fetch the current computation graph."""
    return create_effect_with_trace("graph.snapshot", None, skip_frames=3)


__all__ = [
    "Annotate",
    "Snapshot",
    "Step",
    "graph",
]
