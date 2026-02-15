"""Effect for intercepting programs."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import doeff_vm

from doeff.program import ProgramBase

from .base import Effect, EffectBase

if TYPE_CHECKING:  # pragma: no cover - type-only import to avoid a runtime cycle
    from doeff.program import Program

InterceptTransform = Callable[..., Any]


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
            return (yield doeff_vm.Delegate())

        if isinstance(replacement, (ProgramBase, EffectBase)):
            value = yield replacement
        else:
            value = replacement

        return (yield doeff_vm.Resume(k, value))

    return doeff_vm.WithHandler(handle_intercept, program)


__all__ = ["Intercept"]
