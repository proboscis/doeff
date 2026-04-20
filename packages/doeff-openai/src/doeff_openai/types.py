"""Type definitions for doeff-openai."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal


class OpenAIModel(str, Enum):
    """Supported OpenAI models with their identifiers."""
    # GPT-4 models
    GPT_4_TURBO = "gpt-4-turbo-preview"
    GPT_4_TURBO_2024_04_09 = "gpt-4-turbo-2024-04-09"
    GPT_4_TURBO_VISION = "gpt-4-vision-preview"
    GPT_4 = "gpt-4"
    GPT_4_32K = "gpt-4-32k"
    GPT_4_0125_PREVIEW = "gpt-4-0125-preview"
    GPT_4_1106_PREVIEW = "gpt-4-1106-preview"

    # GPT-3.5 models
    GPT_35_TURBO = "gpt-3.5-turbo"
    GPT_35_TURBO_16K = "gpt-3.5-turbo-16k"
    GPT_35_TURBO_0125 = "gpt-3.5-turbo-0125"
    GPT_35_TURBO_1106 = "gpt-3.5-turbo-1106"

    # Embedding models
    TEXT_EMBEDDING_3_SMALL = "text-embedding-3-small"
    TEXT_EMBEDDING_3_LARGE = "text-embedding-3-large"
    TEXT_EMBEDDING_ADA_002 = "text-embedding-ada-002"

    # O1 models
    O1_PREVIEW = "o1-preview"
    O1_MINI = "o1-mini"


@dataclass(frozen=True)
class TokenUsage:
    """Token usage information for an API call."""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_prompt_tokens: int = 0  # portion of prompt_tokens served from cache

    @property
    def input_tokens(self) -> int:
        """Alias for prompt_tokens."""
        return self.prompt_tokens

    @property
    def output_tokens(self) -> int:
        """Alias for completion_tokens."""
        return self.completion_tokens

    @property
    def fresh_input_tokens(self) -> int:
        """Prompt tokens NOT served from cache (billed at input rate)."""
        return max(self.prompt_tokens - self.cached_prompt_tokens, 0)


@dataclass(frozen=True)
class CostInfo:
    """Cost information for an API call."""
    input_cost: float  # Cost in USD
    output_cost: float  # Cost in USD
    total_cost: float  # Total cost in USD
    model: str
    token_usage: TokenUsage


@dataclass(frozen=True)
class APICallMetadata:
    """Complete metadata for an OpenAI API call."""
    operation: Literal["chat.completion", "embedding", "completion", "moderation"]
    model: str
    timestamp: datetime
    request_id: str | None
    latency_ms: float | None
    token_usage: TokenUsage | None
    cost_info: CostInfo | None
    error: str | None
    stream: bool = False

    def to_graph_metadata(self) -> dict[str, Any]:
        """Convert to Graph effect metadata."""
        metadata = {
            "type": "openai_api_call",
            "operation": self.operation,
            "model": self.model,
            "timestamp": self.timestamp.isoformat(),
            "stream": self.stream,
        }

        if self.request_id:
            metadata["request_id"] = self.request_id

        if self.latency_ms is not None:
            metadata["latency_ms"] = self.latency_ms

        if self.token_usage:
            metadata.update({
                "input_tokens": self.token_usage.input_tokens,
                "output_tokens": self.token_usage.output_tokens,
                "total_tokens": self.token_usage.total_tokens,
            })

        if self.cost_info:
            metadata.update({
                "cost_usd": self.cost_info.total_cost,
                "input_cost_usd": self.cost_info.input_cost,
                "output_cost_usd": self.cost_info.output_cost,
            })

        if self.error:
            metadata["error"] = self.error

        return metadata


@dataclass(frozen=True)
class ModelPricing:
    """Pricing information for a model."""
    input_price_per_1k: float  # USD per 1K fresh input tokens
    output_price_per_1k: float  # USD per 1K output tokens
    context_window: int  # Maximum context size
    max_output_tokens: int | None = None  # Maximum output tokens
    cached_input_price_per_1k: float | None = None
    """USD per 1K cached input tokens. ``None`` means the provider does
    not publish a cached-input rate for this model — any non-zero
    ``cached_prompt_tokens`` then becomes an explicit pricing failure
    (the handler Pass-es so an outer handler can substitute) rather
    than silently billing at the fresh-input rate."""


# Pricing as of 2026-Q2 (prices in USD per 1K tokens).
#
# Sources:
#   https://openai.com/api/pricing/            (official)
#   https://platform.openai.com/docs/models/gpt-5
#   https://platform.openai.com/docs/models/gpt-5-mini
#   https://platform.openai.com/docs/models/gpt-5-nano
#   Helicone cost calculator cross-check.
#
# Cached-input rates reflect OpenAI's 90% prompt-cache discount that is
# advertised alongside the GPT-5 family; earlier generations (GPT-4,
# GPT-3.5, o1) do not publish a separate cached rate so it stays unset
# and the handler fails loudly if caching is ever reported for them.
MODEL_PRICING: dict[str, ModelPricing] = {
    # GPT-5 family (2025-08 launch)
    "gpt-5": ModelPricing(
        0.00125, 0.010, 400000, None, cached_input_price_per_1k=0.000125
    ),
    "gpt-5-mini": ModelPricing(
        0.00025, 0.002, 400000, None, cached_input_price_per_1k=0.000025
    ),
    "gpt-5-nano": ModelPricing(
        0.00005, 0.0004, 400000, None, cached_input_price_per_1k=0.000005
    ),

    # GPT-5.4 family (2026-03 launch; flagship as of 2026-Q2)
    "gpt-5.4": ModelPricing(
        0.00250, 0.015, 1050000, None, cached_input_price_per_1k=0.000250
    ),
    "gpt-5.4-mini": ModelPricing(
        0.00075, 0.0045, 400000, None, cached_input_price_per_1k=0.0000750
    ),
    "gpt-5.4-nano": ModelPricing(
        0.00020, 0.00125, 400000, None, cached_input_price_per_1k=0.0000200
    ),

    # GPT-4 Turbo models
    "gpt-4-turbo-preview": ModelPricing(0.01, 0.03, 128000, 4096),
    "gpt-4-turbo-2024-04-09": ModelPricing(0.01, 0.03, 128000, 4096),
    "gpt-4-vision-preview": ModelPricing(0.01, 0.03, 128000, 4096),
    "gpt-4-0125-preview": ModelPricing(0.01, 0.03, 128000, 4096),
    "gpt-4-1106-preview": ModelPricing(0.01, 0.03, 128000, 4096),

    # GPT-4 models
    "gpt-4": ModelPricing(0.03, 0.06, 8192, 8192),
    "gpt-4-32k": ModelPricing(0.06, 0.12, 32768, 32768),

    # GPT-3.5 Turbo models
    "gpt-3.5-turbo": ModelPricing(0.0005, 0.0015, 16385, 4096),
    "gpt-3.5-turbo-16k": ModelPricing(0.0005, 0.0015, 16385, 4096),
    "gpt-3.5-turbo-0125": ModelPricing(0.0005, 0.0015, 16385, 4096),
    "gpt-3.5-turbo-1106": ModelPricing(0.0005, 0.0015, 16385, 4096),

    # Embedding models (output price is 0 for embeddings)
    "text-embedding-3-small": ModelPricing(0.00002, 0.0, 8191, None),
    "text-embedding-3-large": ModelPricing(0.00013, 0.0, 8191, None),
    "text-embedding-ada-002": ModelPricing(0.0001, 0.0, 8191, None),

    # O1 models
    "o1-preview": ModelPricing(0.015, 0.06, 128000, 32768),
    "o1-mini": ModelPricing(0.003, 0.012, 128000, 65536),
}


@dataclass(frozen=True)
class StreamChunk:
    """A chunk from a streaming response."""
    content: str | None
    role: str | None
    finish_reason: str | None
    index: int
    model: str
    chunk_tokens: int | None = None  # Estimated tokens in this chunk


@dataclass(frozen=True)
class CompletionRequest:
    """Structured chat completion request."""
    messages: list[dict[str, Any]]
    model: str = "gpt-3.5-turbo"
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    stop: str | list[str] | None = None
    stream: bool = False
    user: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    seed: int | None = None


@dataclass(frozen=True)
class EmbeddingRequest:
    """Structured embedding request."""
    input: str | list[str]
    model: str = "text-embedding-3-small"
    encoding_format: Literal["float", "base64"] | None = None
    dimensions: int | None = None  # For text-embedding-3-* models
    user: str | None = None
