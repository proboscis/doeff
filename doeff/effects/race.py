"""Race effects for parallel programs - first to complete wins."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from ._program_types import ProgramLike
from ._validators import ensure_program_tuple
from .base import Effect, EffectBase, create_effect_with_trace, intercept_value


@dataclass(frozen=True)
class RaceEffect(EffectBase):
    """Executes all programs in parallel and yields the first result to complete.

    Returns a tuple of (index, result) where index is the position of the
    winning program in the input tuple.
    """

    programs: tuple[ProgramLike, ...]

    def __post_init__(self) -> None:
        ensure_program_tuple(self.programs, name="programs")

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> RaceEffect:
        programs = intercept_value(self.programs, transform)
        if programs is self.programs:
            return self
        return replace(self, programs=programs)


def race(*programs: ProgramLike) -> RaceEffect:
    return create_effect_with_trace(RaceEffect(programs=tuple(programs)))


def Race(*programs: ProgramLike) -> Effect:
    return create_effect_with_trace(
        RaceEffect(programs=tuple(programs)), skip_frames=3
    )


__all__ = [
    "Race",
    "RaceEffect",
    "race",
]
