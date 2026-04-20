"""Cost calculation and token counting utilities for OpenAI API calls."""

from functools import lru_cache
from typing import Any

import tiktoken

from doeff_openai.types import (
    MODEL_PRICING,
    CostInfo,
    ModelPricing,
    TokenUsage,
)


# Cache for tiktoken encodings
@lru_cache(maxsize=128)
def get_encoding(model: str) -> tiktoken.Encoding:
    """Get the tiktoken encoding for a model, with caching."""
    try:
        # Try to get encoding for the specific model
        return tiktoken.encoding_for_model(model)
    except KeyError:
        # Fall back to cl100k_base for newer models
        if "gpt-4" in model or "gpt-3.5" in model or "embedding" in model:
            return tiktoken.get_encoding("cl100k_base")
        # Default fallback
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str = "gpt-3.5-turbo") -> int:
    """Count tokens in a text string for a specific model."""
    encoding = get_encoding(model)
    return len(encoding.encode(text))


def count_message_tokens(
    messages: list[dict[str, Any]],
    model: str = "gpt-3.5-turbo"
) -> int:
    """
    Count tokens in a list of messages for chat completion.
    
    Based on OpenAI's guidelines for counting tokens in chat messages.
    """
    encoding = get_encoding(model)

    # Token counts for message formatting
    if "gpt-3.5-turbo" in model:
        tokens_per_message = 3  # <|start|>{role}\n{content}<|end|>\n
        tokens_per_name = 1
    elif "gpt-4" in model:
        tokens_per_message = 3
        tokens_per_name = 1
    else:
        # Default for unknown models
        tokens_per_message = 3
        tokens_per_name = 1

    num_tokens = 0
    for message in messages:
        num_tokens += tokens_per_message
        for key, value in message.items():
            if key == "role":
                num_tokens += len(encoding.encode(value))
            elif key == "content":
                if value is not None:
                    if isinstance(value, str):
                        num_tokens += len(encoding.encode(value))
                    elif isinstance(value, list):
                        # For vision models with image content
                        for item in value:
                            if isinstance(item, dict) and item.get("type") == "text":
                                num_tokens += len(encoding.encode(item.get("text", "")))
                            # Image tokens are estimated
                            elif isinstance(item, dict) and item.get("type") == "image_url":
                                # Rough estimate for image tokens
                                num_tokens += 85  # Base cost for an image
            elif key == "name":
                num_tokens += tokens_per_name + len(encoding.encode(value))
            elif key == "function_call" or key == "tool_calls":
                # Function calls and tool calls
                if isinstance(value, dict):
                    for k, v in value.items():
                        if isinstance(v, str):
                            num_tokens += len(encoding.encode(v))
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            for k, v in item.items():
                                if isinstance(v, str):
                                    num_tokens += len(encoding.encode(v))
                                elif isinstance(v, dict):
                                    for kk, vv in v.items():
                                        if isinstance(vv, str):
                                            num_tokens += len(encoding.encode(vv))

    # Add 3 tokens for the assistant's reply priming
    num_tokens += 3

    return num_tokens


def count_embedding_tokens(
    input: str | list[str],
    model: str = "text-embedding-3-small"
) -> int:
    """Count tokens for embedding input."""
    encoding = get_encoding(model)

    if isinstance(input, str):
        return len(encoding.encode(input))
    return sum(len(encoding.encode(text)) for text in input)


class PricingError(ValueError):
    """Base class for pricing-lookup failures."""


class UnknownModelPricingError(PricingError):
    """Raised when no entry matches the requested model."""

    def __init__(self, model: str) -> None:
        super().__init__(f"no pricing entry for model {model!r}")
        self.model = model


class MissingCachedPricingError(PricingError):
    """Raised when cached_prompt_tokens > 0 but the model's pricing has
    no cached_input_price_per_1k."""

    def __init__(self, model: str, cached_tokens: int) -> None:
        super().__init__(
            f"model {model!r} reported {cached_tokens} cached_prompt_tokens "
            f"but has no cached_input_price_per_1k"
        )
        self.model = model
        self.cached_tokens = cached_tokens


def calculate_cost(
    model: str,
    token_usage: TokenUsage,
) -> CostInfo:
    """Calculate the billed cost of an API call.

    Raises:
        UnknownModelPricingError: when the model has no pricing entry
            (neither exact nor prefix match). No silent fall-back is
            provided — the caller is expected to handle this explicitly
            (e.g. by yielding a :class:`doeff_openai.effects.CalculateCost`
            effect so an outer handler can substitute a value).
        MissingCachedPricingError: when usage reports cached tokens but
            the pricing entry has no cached rate — charging at the fresh
            input rate would silently overbill the user.
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        for model_key, model_pricing in MODEL_PRICING.items():
            if model.startswith(model_key) or model_key in model:
                pricing = model_pricing
                break

    if pricing is None:
        raise UnknownModelPricingError(model)

    cached = token_usage.cached_prompt_tokens
    if cached > 0 and pricing.cached_input_price_per_1k is None:
        raise MissingCachedPricingError(model, cached)

    fresh_input = token_usage.fresh_input_tokens
    input_cost = (fresh_input / 1000) * pricing.input_price_per_1k
    if cached > 0:
        # pricing.cached_input_price_per_1k is not None here (guarded above)
        input_cost += (cached / 1000) * pricing.cached_input_price_per_1k
    output_cost = (token_usage.output_tokens / 1000) * pricing.output_price_per_1k
    total_cost = input_cost + output_cost

    return CostInfo(
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=total_cost,
        model=model,
        token_usage=token_usage,
    )


def estimate_cost(
    model: str,
    input_text: str | None = None,
    output_text: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> CostInfo:
    """
    Estimate the cost of an API call.
    
    Can provide either:
    - input_text and output_text for simple text
    - messages for chat completion
    - input_tokens and output_tokens directly
    """
    # Calculate or use provided token counts
    if input_tokens is None:
        if messages:
            input_tokens = count_message_tokens(messages, model)
        elif input_text:
            input_tokens = count_tokens(input_text, model)
        else:
            input_tokens = 0

    if output_tokens is None:
        if output_text:
            output_tokens = count_tokens(output_text, model)
        else:
            output_tokens = 0

    token_usage = TokenUsage(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )

    return calculate_cost(model, token_usage)


def get_model_pricing(model: str) -> ModelPricing | None:
    """Get pricing information for a model."""
    pricing = MODEL_PRICING.get(model)
    if not pricing:
        # Try to find a matching model by prefix
        for model_key, model_pricing in MODEL_PRICING.items():
            if model.startswith(model_key) or model_key in model:
                return model_pricing
    return pricing


def estimate_max_cost(
    model: str,
    max_tokens: int | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> float:
    """Estimate the maximum cost for a completion request.

    Raises:
        UnknownModelPricingError: when the model has no pricing entry.
    """
    pricing = get_model_pricing(model)
    if pricing is None:
        raise UnknownModelPricingError(model)

    if messages:
        input_tokens = count_message_tokens(messages, model)
    else:
        input_tokens = 0

    if max_tokens is None:
        max_tokens = pricing.max_output_tokens or 4096

    input_cost = (input_tokens / 1000) * pricing.input_price_per_1k
    output_cost = (max_tokens / 1000) * pricing.output_price_per_1k

    return input_cost + output_cost
