"""Effect for intercepting programs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from doeff.program import ProgramBase

from .base import Effect, EffectBase, create_effect_with_trace, intercept_value

if TYPE_CHECKING:  # pragma: no cover - type-only import to avoid a runtime cycle
    from doeff.program import Program


@dataclass(frozen=True)
class InterceptEffect(EffectBase):
    """Intercepts a program and applies transforms to its yielded effects."""

    program: Program
    transforms: tuple[Callable[[Effect], Effect | Program], ...]

    def __post_init__(self):
        if not isinstance(self.program, ProgramBase):
            raise TypeError(
                f"program must be a Program instance, got {type(self.program)}"
            )

    def intercept(self, transform: Callable[[Effect], Effect | Program]) -> InterceptEffect:
        intercepted_program = intercept_value(self.program, transform)
        if intercepted_program is self.program:
            return self
        return replace(self, program=intercepted_program)


def intercept_program_effect(
    program: Program,
    transforms: tuple[Callable[[Effect], Effect | Program], ...],
) -> InterceptEffect:
    """Helper to create an InterceptEffect with trace metadata."""

    return create_effect_with_trace(InterceptEffect(program=program, transforms=transforms))


__all__ = ["InterceptEffect", "intercept_program_effect"]
