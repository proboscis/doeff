"""Cost calculation helpers for Gemini models."""

from __future__ import annotations

from dataclasses import dataclass

from doeff import Tell, do

from .types import CostInfo, GeminiCallResult, GeminiCostEstimate


@dataclass(frozen=True)
class ModelPricing:
    """Pricing information for a Gemini model (USD per million tokens)."""

    text_input: float
    text_output: float
    image_input: float = 0.0
    image_output: float = 0.0


_MODEL_PRICING: dict[str, ModelPricing] = {
    "gemini-2.5-flash": ModelPricing(
        text_input=0.30, text_output=2.50, image_input=0.0, image_output=0.0
    ),
    "gemini-2.5-flash-image-preview": ModelPricing(
        text_input=0.30, text_output=2.50, image_input=0.30, image_output=30.0
    ),
    "gemini-2.5-pro": ModelPricing(
        text_input=12.50, text_output=150.0, image_input=0.0, image_output=0.0
    ),
    "gemini-2.0-flash": ModelPricing(
        text_input=1.25, text_output=10.0, image_input=0.0, image_output=0.0
    ),
    "gemini-1.5-flash": ModelPricing(
        text_input=1.25, text_output=10.0, image_input=0.0, image_output=0.0
    ),
    "gemini-1.5-pro": ModelPricing(
        text_input=12.50, text_output=150.0, image_input=0.0, image_output=0.0
    ),
    # Gemini 3 Pro Image (Nano Banana Pro) pricing
    "gemini-3-pro-image-preview": ModelPricing(
        text_input=2.00,
        text_output=12.00,
        image_input=2.00,
        image_output=120.00,
    ),
    # Gemini 3 Flash Preview — $0.50/$3.00 per 1M tokens (standard context)
    "gemini-3-flash-preview": ModelPricing(text_input=0.50, text_output=3.00),
    # Gemini 3.1 Pro Preview — $2.00/$12.00 per 1M tokens (standard context ≤200K)
    "gemini-3.1-pro-preview": ModelPricing(text_input=2.00, text_output=12.00),
}


def _normalize_model_name(model: str) -> str:
    base = model.split(":", 1)[0]
    if base.endswith("-latest"):
        base = base[:-7]
    if base.endswith("-exp"):
        base = base[:-4]
    return base


def calculate_cost(model: str, usage: dict[str, int]) -> CostInfo:
    """Calculate Gemini cost information for the given usage."""

    normalized = _normalize_model_name(model)
    pricing = _MODEL_PRICING.get(normalized)
    if pricing is None:
        raise ValueError(f"No pricing information available for model '{model}'")

    text_in_tokens = usage.get("text_input_tokens", 0) or 0
    text_out_tokens = usage.get("text_output_tokens", 0) or 0
    image_in_tokens = usage.get("image_input_tokens", 0) or 0
    image_out_tokens = usage.get("image_output_tokens", 0) or 0

    total_tokens = usage.get("total_tokens")
    if (
        pricing.image_output > 0
        and image_out_tokens == 0
        and total_tokens is not None
        and total_tokens > (text_in_tokens + text_out_tokens + image_in_tokens)
    ):
        # Image-output token counts are sometimes omitted; treat the remaining
        # tokens as image output when the model has image-output pricing.
        image_out_tokens = total_tokens - (text_in_tokens + text_out_tokens + image_in_tokens)

    text_in_cost = (text_in_tokens / 1_000_000) * pricing.text_input
    text_out_cost = (text_out_tokens / 1_000_000) * pricing.text_output
    image_in_cost = (image_in_tokens / 1_000_000) * pricing.image_input
    image_out_cost = (image_out_tokens / 1_000_000) * pricing.image_output

    total_cost = text_in_cost + text_out_cost + image_in_cost + image_out_cost

    return CostInfo(
        total_cost=total_cost,
        text_input_cost=text_in_cost,
        text_output_cost=text_out_cost,
        image_input_cost=image_in_cost,
        image_output_cost=image_out_cost,
    )


@do
def gemini_cost_calculator__default(
    call_result: GeminiCallResult,
) -> GeminiCostEstimate:
    """Default Kleisli cost calculator backed by the built-in pricing table."""

    usage = call_result.payload.get("usage") if isinstance(call_result.payload, dict) else None
    if not usage:
        yield Tell("Gemini cost calculation failed: usage metadata missing")
        raise ValueError("Gemini usage metadata missing for cost calculation")

    cost_info = calculate_cost(call_result.model_name, usage)
    return GeminiCostEstimate(cost_info=cost_info, raw_usage=usage)


__all__ = [
    "ModelPricing",
    "calculate_cost",
    "gemini_cost_calculator__default",
]
