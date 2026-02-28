"""Cost calculation helpers for Gemini models."""


from dataclasses import dataclass

from doeff import do

from .types import CostInfo, GeminiCallResult, GeminiCostEstimate


@dataclass(frozen=True)
class ModelPricing:
    """Pricing information for a Gemini model (USD per million tokens)."""

    text_input: float
    text_output: float
    image_input: float = 0.0
    image_output: float = 0.0
    long_context_threshold: int | None = None
    long_context_text_input: float | None = None
    long_context_text_output: float | None = None


class UnknownModelPricingError(ValueError):
    """Raised when no default pricing exists for a model."""


_MODEL_PRICING: dict[str, ModelPricing] = {
    # https://ai.google.dev/pricing (checked 2026-02-28)
    # <= 200k input tokens: $1.25 / 1M input, $10.00 / 1M output
    # > 200k input tokens:  $2.50 / 1M input, $15.00 / 1M output
    "gemini-2.5-pro": ModelPricing(
        text_input=1.25,
        text_output=10.0,
        long_context_threshold=200_000,
        long_context_text_input=2.50,
        long_context_text_output=15.0,
    ),
    # <= 200k input tokens: $0.30 / 1M input, $2.50 / 1M output
    # > 200k input tokens:  $0.60 / 1M input, $2.50 / 1M output
    "gemini-2.5-flash": ModelPricing(
        text_input=0.30,
        text_output=2.50,
        long_context_threshold=200_000,
        long_context_text_input=0.60,
        long_context_text_output=2.50,
    ),
    # Current image-generation pricing is $0.039/image, equivalent to $30/1M image-output tokens.
    "gemini-2.5-flash-image-preview": ModelPricing(
        text_input=0.30,
        text_output=2.50,
        image_input=0.0,
        image_output=30.0,
        long_context_threshold=200_000,
        long_context_text_input=0.60,
        long_context_text_output=2.50,
    ),
    # Token pricing + image-generation pricing on https://ai.google.dev/pricing.
    # Image generation: $0.039/image => equivalent to $30/1M image-output tokens.
    "gemini-2.0-flash": ModelPricing(
        text_input=0.10,
        text_output=0.40,
        image_input=0.0,
        image_output=30.0,
    ),
    "gemini-2.0-flash-preview-image-generation": ModelPricing(
        text_input=0.10,
        text_output=0.40,
        image_input=0.0,
        image_output=30.0,
    ),
    "gemini-2.0-flash-lite": ModelPricing(
        text_input=0.075,
        text_output=0.30,
    ),
    # 1.5 models are legacy/deprecated on current pricing page but retained for backward compatibility.
    # Rates from the last official ai.google.dev/pricing listing:
    # <= 128k input tokens, then higher long-context tier above 128k.
    "gemini-1.5-flash": ModelPricing(
        text_input=0.075,
        text_output=0.30,
        long_context_threshold=128_000,
        long_context_text_input=0.15,
        long_context_text_output=0.60,
    ),
    "gemini-1.5-pro": ModelPricing(
        text_input=1.25,
        text_output=5.0,
        long_context_threshold=128_000,
        long_context_text_input=2.50,
        long_context_text_output=10.0,
    ),
    # Gemini 3 Pro Image (Nano Banana Pro) legacy/preview pricing.
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
    for suffix in ("-001", "-002", "-003"):
        if base.endswith(suffix):
            candidate = base[: -len(suffix)]
            if candidate in _MODEL_PRICING:
                return candidate
    return base


def _resolve_effective_text_rates(pricing: ModelPricing, text_input_tokens: int) -> tuple[float, float]:
    if (
        pricing.long_context_threshold is not None
        and text_input_tokens > pricing.long_context_threshold
    ):
        return (
            pricing.long_context_text_input
            if pricing.long_context_text_input is not None
            else pricing.text_input,
            pricing.long_context_text_output
            if pricing.long_context_text_output is not None
            else pricing.text_output,
        )
    return pricing.text_input, pricing.text_output


def get_model_pricing(model: str) -> ModelPricing | None:
    """Return default pricing for a model, if known."""

    normalized = _normalize_model_name(model)
    return _MODEL_PRICING.get(normalized)


def calculate_cost(model: str, usage: dict[str, int]) -> CostInfo:
    """Calculate Gemini cost information for the given usage."""

    pricing = get_model_pricing(model)
    if pricing is None:
        raise UnknownModelPricingError(f"No pricing information available for model '{model}'")

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

    effective_text_input_rate, effective_text_output_rate = _resolve_effective_text_rates(
        pricing, text_in_tokens
    )

    text_in_cost = (text_in_tokens / 1_000_000) * effective_text_input_rate
    text_out_cost = (text_out_tokens / 1_000_000) * effective_text_output_rate
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


def calculate_known_model_cost(call_result: GeminiCallResult) -> GeminiCostEstimate | None:
    """Calculate cost for a known model, or return ``None`` when model pricing is unknown."""

    usage = call_result.payload.get("usage") if isinstance(call_result.payload, dict) else None
    if not usage:
        raise ValueError("Gemini usage metadata missing for cost calculation")

    try:
        cost_info = calculate_cost(call_result.model_name, usage)
    except UnknownModelPricingError:
        return None

    return GeminiCostEstimate(cost_info=cost_info, raw_usage=usage)


@do
def gemini_cost_calculator__default(
    call_result: GeminiCallResult,
) -> GeminiCostEstimate:
    """Legacy Kleisli wrapper around default known-model pricing."""

    estimate = calculate_known_model_cost(call_result)
    if estimate is None:
        raise UnknownModelPricingError(
            f"No pricing information available for model '{call_result.model_name}'"
        )
    return estimate


__all__ = [
    "ModelPricing",
    "UnknownModelPricingError",
    "calculate_cost",
    "calculate_known_model_cost",
    "get_model_pricing",
    "gemini_cost_calculator__default",
]
