"""Pure effect - represents an immediate value (Pure case of Free monad)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .base import Effect, EffectBase, create_effect_with_trace

if TYPE_CHECKING:
    from doeff.program import Program


@dataclass(frozen=True)
class PureEffect(EffectBase):
    """
    Represents an immediate value without performing any effect.

    This is the Pure case of the Free monad, used for wrapping plain values
    into the effect system. When executed, it immediately returns its value
    without side effects.
    """

    value: Any

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> PureEffect:
        """Pure effect has no nested programs, so intercept returns self."""
        return self


def Pure(value: Any) -> Effect:  # noqa: N802
    """
    Create a PureEffect with creation trace context.

    Args:
        value: The value to wrap

    Returns:
        PureEffect with trace information
    """
    return create_effect_with_trace(PureEffect(value=value), skip_frames=3)


__all__ = [
    "Pure",
    "PureEffect",
]
