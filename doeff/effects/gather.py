"""Gather effects for parallel programs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from ._program_types import ProgramLike
from ._validators import ensure_program_tuple
from .base import Effect, EffectBase, create_effect_with_trace, intercept_value


@dataclass(frozen=True)
class GatherEffect(EffectBase):
    """Executes all programs in parallel and yields their results as a list."""

    programs: tuple[ProgramLike, ...]

    def __post_init__(self) -> None:
        ensure_program_tuple(self.programs, name="programs")

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> GatherEffect:
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
    "Gather",
    "GatherEffect",
    "gather",
]
