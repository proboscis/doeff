"""Effect for intercepting programs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from doeff.program import ProgramBase

from .base import Effect, EffectBase, create_effect_with_trace

if TYPE_CHECKING:  # pragma: no cover - type-only import to avoid a runtime cycle
    from doeff.program import Program

InterceptTransform = Callable[..., Any]


@dataclass(frozen=True)
class InterceptEffect(EffectBase):
    program: Program
    transforms: tuple[InterceptTransform, ...]

    def __post_init__(self):
        if not isinstance(self.program, ProgramBase):
            raise TypeError(
                f"program must be a Program instance, got {type(self.program)}"
            )


def intercept_program_effect(
    program: Program,
    transforms: tuple[InterceptTransform, ...],
) -> InterceptEffect:
    return create_effect_with_trace(InterceptEffect(program=program, transforms=transforms))


def Intercept(
    program: Program,
    *transforms: InterceptTransform,
) -> Effect:
    if not transforms:
        raise ValueError("Intercept requires at least one transform function")
    return create_effect_with_trace(
        InterceptEffect(program=program, transforms=transforms),
        skip_frames=3,
    )


__all__ = ["Intercept", "InterceptEffect", "intercept_program_effect"]
