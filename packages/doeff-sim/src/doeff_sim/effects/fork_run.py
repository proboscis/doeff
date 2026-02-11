"""Run a sub-program in an isolated simulation fork."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff.effects.base import Effect, EffectBase, create_effect_with_trace
from doeff.program import ProgramBase


@dataclass(frozen=True)
class ForkRunEffect(EffectBase):
    """Run a program in an isolated simulation fork."""

    program: Any
    start_time: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.program, ProgramBase):
            raise TypeError(f"program must be ProgramBase, got {type(self.program).__name__}")
        if self.start_time is not None:
            object.__setattr__(self, "start_time", float(self.start_time))


def fork_run(program: Any, *, start_time: float | None = None) -> ForkRunEffect:
    return create_effect_with_trace(ForkRunEffect(program=program, start_time=start_time))


def ForkRun(program: Any, *, start_time: float | None = None) -> Effect:
    return create_effect_with_trace(
        ForkRunEffect(program=program, start_time=start_time),
        skip_frames=3,
    )


__all__ = [
    "ForkRun",
    "ForkRunEffect",
    "fork_run",
]
