"""Cost-calculation effect for doeff-gemini."""


from dataclasses import dataclass

from doeff.effects.base import EffectBase

from doeff_gemini.types import GeminiCallResult


@dataclass(frozen=True)
class GeminiCalculateCost(EffectBase):
    """Request a cost estimate for a Gemini API call result."""

    call_result: GeminiCallResult


__all__ = ["GeminiCalculateCost"]
