"""Effect for intercepting programs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import doeff_vm

from doeff.program import ProgramBase
from doeff.types import EffectBase

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
            raise TypeError(f"program must be a Program instance, got {type(self.program)}")


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

    def handle_intercept(effect, k):
        replacement = None
        for transform in transforms:
            candidate = transform(effect)
            if candidate is not None:
                replacement = candidate
                break

        if replacement is None:
            yield doeff_vm.Delegate()
            return

        if isinstance(replacement, (ProgramBase, EffectBase)):
            value = yield replacement
        else:
            value = replacement

        return (yield doeff_vm.Resume(k, value))

    return doeff_vm.WithHandler(handle_intercept, program)


__all__ = ["Intercept", "InterceptEffect", "intercept_program_effect"]
