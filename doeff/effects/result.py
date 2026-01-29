"""Result/error handling effects."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from ._program_types import ProgramLike
from ._validators import ensure_program_like
from .base import Effect, EffectBase, create_effect_with_trace, intercept_value


@dataclass(frozen=True)
class ResultSafeEffect(EffectBase):
    """Runs the sub-program and yields a Result for success/failure."""

    sub_program: ProgramLike

    def __post_init__(self) -> None:
        ensure_program_like(self.sub_program, name="sub_program")

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> ResultSafeEffect:
        sub_program = intercept_value(self.sub_program, transform)
        if sub_program is self.sub_program:
            return self
        return replace(self, sub_program=sub_program)


def safe(sub_program: ProgramLike) -> ResultSafeEffect:
    return create_effect_with_trace(ResultSafeEffect(sub_program=sub_program))


def Safe(sub_program: ProgramLike) -> Effect:
    return create_effect_with_trace(
        ResultSafeEffect(sub_program=sub_program),
        skip_frames=3,
    )


__all__ = [
    "ResultSafeEffect",
    "Safe",
    "safe",
]
