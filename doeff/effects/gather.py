"""Gather effects for parallel programs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Mapping
from typing import Callable, Tuple

from ._program_types import ProgramLike
from .base import Effect, EffectBase, create_effect_with_trace, intercept_value


@dataclass(frozen=True)
class GatherEffect(EffectBase):
    """Executes all programs in parallel and yields their results as a list."""

    programs: Tuple[ProgramLike, ...]

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "GatherEffect":
        programs = intercept_value(self.programs, transform)
        if programs is self.programs:
            return self
        return replace(self, programs=programs)


@dataclass(frozen=True)
class GatherDictEffect(EffectBase):
    """Runs the program mapping and yields a dict keyed by the supplied names."""

    programs: Mapping[str, ProgramLike]

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "GatherDictEffect":
        programs = intercept_value(self.programs, transform)
        if programs is self.programs:
            return self
        return replace(self, programs=programs)


def gather(*programs: ProgramLike) -> GatherEffect:
    return create_effect_with_trace(GatherEffect(programs=tuple(programs)))


def gather_dict(programs: Mapping[str, ProgramLike]) -> GatherDictEffect:
    return create_effect_with_trace(GatherDictEffect(programs=programs))


def Gather(*programs: ProgramLike) -> Effect:
    return create_effect_with_trace(
        GatherEffect(programs=tuple(programs)), skip_frames=3
    )


def GatherDict(programs: Mapping[str, ProgramLike]) -> Effect:
    return create_effect_with_trace(GatherDictEffect(programs=programs), skip_frames=3)


__all__ = [
    "GatherEffect",
    "GatherDictEffect",
    "gather",
    "gather_dict",
    "Gather",
    "GatherDict",
]
