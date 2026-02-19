"""Effects for introspecting the execution debug context."""

from __future__ import annotations

from dataclasses import dataclass

from .base import EffectBase


@dataclass(frozen=True)
class GetDebugContextEffect(EffectBase):
    """Yields the current debug context including K stack, Kleisli stack, and effect call tree."""


def GetDebugContext() -> GetDebugContextEffect:
    """Create an effect that yields the current DebugContext.

    Returns a DebugContext containing:
    - kleisli_stack: tuple of KleisliStackEntry showing @do function call chain
    - k_frames: tuple of KFrameInfo showing all continuation frames
    - effect_call_tree: EffectCallNode tree showing effect origins
    - current_effect: name of the current effect being handled
    """
    return GetDebugContextEffect()


__all__ = [
    "GetDebugContext",
    "GetDebugContextEffect",
]
