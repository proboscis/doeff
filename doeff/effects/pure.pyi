from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doeff.effects.base import Effect, EffectBase
from doeff.program import Program

class PureEffect(EffectBase):
    value: Any

    def intercept(self, transform: Callable[[Effect], Effect | Program]) -> PureEffect: ...

def Pure(value: Any) -> Effect: ...  # noqa: N802

__all__ = ["Pure", "PureEffect"]
