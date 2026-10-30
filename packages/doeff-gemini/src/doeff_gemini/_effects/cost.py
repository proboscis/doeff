"""Cost-calculation effect for doeff-gemini."""


from dataclasses import dataclass

from doeff import EffectBase
from doeff_gemini._types import GeminiCallResult, GeminiCostEstimate


@dataclass(frozen=True)
class GeminiCalculateCost(EffectBase[GeminiCostEstimate]):
    """Request a cost estimate for a Gemini API call result."""

    call_result: GeminiCallResult


__all__ = ["GeminiCalculateCost"]
