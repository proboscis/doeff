"""Graph tracking effects."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Dict

from ._program_types import ProgramLike
from .base import Effect, EffectBase, create_effect_with_trace, intercept_value


@dataclass(frozen=True)
class GraphStepEffect(EffectBase):
    """Appends a node to the graph and yields the provided value."""

    value: Any
    meta: Dict[str, Any]

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "GraphStepEffect":
        return self


@dataclass(frozen=True)
class GraphAnnotateEffect(EffectBase):
    """Updates metadata on the latest graph step and completes without a value."""

    meta: Dict[str, Any]

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "GraphAnnotateEffect":
        return self


@dataclass(frozen=True)
class GraphSnapshotEffect(EffectBase):
    """Captures the current computation graph and yields it."""

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "GraphSnapshotEffect":
        return self


@dataclass(frozen=True)
class GraphCaptureEffect(EffectBase):
    """Runs a program and yields both its value and the graph it produced."""

    program: ProgramLike

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "GraphCaptureEffect":
        program = intercept_value(self.program, transform)
        if program is self.program:
            return self
        return replace(self, program=program)


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
