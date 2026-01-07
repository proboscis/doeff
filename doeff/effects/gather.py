"""Gather effects for parallel programs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Tuple

from ._program_types import ProgramLike
from .base import Effect, EffectBase, create_effect_with_trace, intercept_value
from ._validators import ensure_program_tuple


@dataclass(frozen=True)
class GatherEffect(EffectBase):
    """Executes all programs in parallel and yields their results as a list."""

    programs: Tuple[ProgramLike, ...]

    def __post_init__(self) -> None:
        ensure_program_tuple(self.programs, name="programs")

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "GatherEffect":
        programs = intercept_value(self.programs, transform)
        if programs is self.programs:
            return self
        return replace(self, programs=programs)


def gather(*programs: ProgramLike) -> GatherEffect:
    return create_effect_with_trace(GatherEffect(programs=tuple(programs)))


def Gather(*programs: ProgramLike) -> Effect:
    return create_effect_with_trace(
        GatherEffect(programs=tuple(programs)), skip_frames=3
    )


__all__ = [
    "GatherEffect",
    "gather",
    "Gather",
]
