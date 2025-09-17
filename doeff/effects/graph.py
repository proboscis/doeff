"""Graph tracking effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from .base import Effect, EffectBase, create_effect_with_trace


@dataclass(frozen=True)
class GraphStepEffect(EffectBase):
    value: Any
    meta: Dict[str, Any]


@dataclass(frozen=True)
class GraphAnnotateEffect(EffectBase):
    meta: Dict[str, Any]


@dataclass(frozen=True)
class GraphSnapshotEffect(EffectBase):
    pass


class graph:
    """Graph tracking effects."""

    @staticmethod
    def step(value: Any, meta: dict[str, Any] | None = None) -> GraphStepEffect:
        return create_effect_with_trace(
            GraphStepEffect(value=value, meta=dict(meta or {}))
        )

    @staticmethod
    def annotate(meta: dict[str, Any]) -> GraphAnnotateEffect:
        return create_effect_with_trace(GraphAnnotateEffect(meta=dict(meta)))

    @staticmethod
    def snapshot() -> GraphSnapshotEffect:
        return create_effect_with_trace(GraphSnapshotEffect())


# Uppercase aliases
def Step(value: Any, meta: dict[str, Any] | None = None) -> Effect:
    return create_effect_with_trace(
        GraphStepEffect(value=value, meta=dict(meta or {})), skip_frames=3
    )


def Annotate(meta: dict[str, Any]) -> Effect:
    return create_effect_with_trace(GraphAnnotateEffect(meta=dict(meta)), skip_frames=3)


def Snapshot() -> Effect:
    return create_effect_with_trace(GraphSnapshotEffect(), skip_frames=3)


__all__ = [
    "GraphStepEffect",
    "GraphAnnotateEffect",
    "GraphSnapshotEffect",
    "Annotate",
    "Snapshot",
    "Step",
    "graph",
]
