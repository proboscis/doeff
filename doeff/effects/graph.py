"""Graph tracking effects."""


from dataclasses import dataclass
from typing import Any

from ._program_types import ProgramLike
from ._validators import ensure_dict_str_any, ensure_program_like
from .base import Effect, EffectBase


@dataclass(frozen=True)
class GraphStepEffect(EffectBase):
    """Appends a node to the graph and yields the provided value."""

    value: Any
    meta: dict[str, Any]

    def __post_init__(self) -> None:
        ensure_dict_str_any(self.meta, name="meta")


@dataclass(frozen=True)
class GraphAnnotateEffect(EffectBase):
    """Updates metadata on the latest graph step and completes without a value."""

    meta: dict[str, Any]

    def __post_init__(self) -> None:
        ensure_dict_str_any(self.meta, name="meta")


@dataclass(frozen=True)
class GraphSnapshotEffect(EffectBase):
    """Captures the current computation graph and yields it."""


@dataclass(frozen=True)
class GraphCaptureEffect(EffectBase):
    """Runs a program and yields both its value and the graph it produced."""

    program: ProgramLike

    def __post_init__(self) -> None:
        ensure_program_like(self.program, name="program")


class graph:
    """Graph tracking effects."""

    @staticmethod
    def step(value: Any, meta: dict[str, Any] | None = None) -> GraphStepEffect:
        return GraphStepEffect(value=value, meta=dict(meta or {}))

    @staticmethod
    def annotate(meta: dict[str, Any]) -> GraphAnnotateEffect:
        return GraphAnnotateEffect(meta=dict(meta))

    @staticmethod
    def snapshot() -> GraphSnapshotEffect:
        return GraphSnapshotEffect()

    @staticmethod
    def capture(program: ProgramLike) -> GraphCaptureEffect:
        return GraphCaptureEffect(program=program)


# Uppercase aliases
def Step(value: Any, meta: dict[str, Any] | None = None) -> Effect:
    return GraphStepEffect(value=value, meta=dict(meta or {}))


def Annotate(meta: dict[str, Any]) -> Effect:
    return GraphAnnotateEffect(meta=dict(meta))


def Snapshot() -> Effect:
    return GraphSnapshotEffect()


def CaptureGraph(program: ProgramLike) -> Effect:
    return GraphCaptureEffect(program=program)


def capture_graph(program: ProgramLike) -> Effect:
    return graph.capture(program)


capture = CaptureGraph


__all__ = [
    "Annotate",
    "CaptureGraph",
    "GraphAnnotateEffect",
    "GraphCaptureEffect",
    "GraphSnapshotEffect",
    "GraphStepEffect",
    "Snapshot",
    "Step",
    "capture",
    "capture_graph",
    "graph",
]
