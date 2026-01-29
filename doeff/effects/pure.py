"""Pure effect - represents an immediate value (Pure case of Free monad)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import Effect, EffectBase, create_effect_with_trace


@dataclass(frozen=True)
class PureEffect(EffectBase):
    """
    Represents an immediate value without performing any effect.

    This is the Pure case of the Free monad, used for wrapping plain values
    into the effect system. When executed, it immediately returns its value
    without side effects.
    """

    value: Any


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
