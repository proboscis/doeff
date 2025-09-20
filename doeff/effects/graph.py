"""Graph tracking effects."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any, Dict

from ._program_types import ProgramLike
from .base import Effect, EffectBase, create_effect_with_trace


@dataclass(frozen=True)
class GraphStepEffect(EffectBase):
    """Appends a node to the graph and yields the provided value."""

    value: Any
    meta: Dict[str, Any]


@dataclass(frozen=True)
class GraphAnnotateEffect(EffectBase):
    """Updates metadata on the latest graph step and completes without a value."""

    meta: Dict[str, Any]


@dataclass(frozen=True)
class GraphSnapshotEffect(EffectBase):
    """Captures the current computation graph and yields it."""

    pass


@dataclass(frozen=True)
class GraphCaptureEffect(EffectBase):
    """Runs a program and yields both its value and the graph it produced."""

    program: ProgramLike


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

    @staticmethod
    def capture(program: ProgramLike) -> GraphCaptureEffect:
        return create_effect_with_trace(GraphCaptureEffect(program=program))


# Uppercase aliases
def Step(value: Any, meta: dict[str, Any] | None = None) -> Effect:
    return create_effect_with_trace(
        GraphStepEffect(value=value, meta=dict(meta or {})), skip_frames=3
    )


def Annotate(meta: dict[str, Any]) -> Effect:
    return create_effect_with_trace(GraphAnnotateEffect(meta=dict(meta)), skip_frames=3)


def Snapshot() -> Effect:
    return create_effect_with_trace(GraphSnapshotEffect(), skip_frames=3)


def CaptureGraph(program: ProgramLike) -> Effect:
    return create_effect_with_trace(
        GraphCaptureEffect(program=program), skip_frames=3
    )


def capture_graph(program: ProgramLike) -> Effect:
    return graph.capture(program)


capture = CaptureGraph


__all__ = [
    "GraphStepEffect",
    "GraphAnnotateEffect",
    "GraphSnapshotEffect",
    "GraphCaptureEffect",
    "Annotate",
    "Snapshot",
    "Step",
    "CaptureGraph",
    "capture",
    "capture_graph",
    "graph",
]
