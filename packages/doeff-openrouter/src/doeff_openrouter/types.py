"""Type definitions for the :mod:`doeff_openrouter` package."""


from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True)
class TokenUsage:
    """Token usage information returned by OpenRouter."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int | None = None

    @property
    def input_tokens(self) -> int:
        """Alias for the number of prompt tokens."""
        return self.prompt_tokens

    @property
    def output_tokens(self) -> int:
        """Alias for the number of completion tokens."""
        return self.completion_tokens


@dataclass(frozen=True)
class CostInfo:
    """Cost metadata if OpenRouter reports it in the response."""

    total_cost: float
    currency: str = "USD"
    prompt_cost: float | None = None
    completion_cost: float | None = None


@dataclass(frozen=True)
class APICallMetadata:
    """Captured metadata for an OpenRouter API invocation."""

    operation: Literal["chat.completion"]
    model: str
    timestamp: datetime
    request_id: str | None
    latency_ms: float | None
    token_usage: TokenUsage | None
    cost_info: CostInfo | None
    error: str | None
    stream: bool = False
    provider: str | None = None

    def to_graph_metadata(self) -> dict[str, Any]:
        """Convert the dataclass into a dictionary for Graph steps."""
        metadata: dict[str, Any] = {
            "type": "openrouter_api_call",
            "operation": self.operation,
            "model": self.model,
            "timestamp": self.timestamp.isoformat(),
            "stream": self.stream,
        }

        if self.request_id:
            metadata["request_id"] = self.request_id
        if self.latency_ms is not None:
            metadata["latency_ms"] = self.latency_ms
        if self.provider:
            metadata["provider"] = self.provider
        if self.token_usage:
            metadata.update(
                {
                    "input_tokens": self.token_usage.input_tokens,
                    "output_tokens": self.token_usage.output_tokens,
                    "total_tokens": self.token_usage.total_tokens,
                }
            )
            if self.token_usage.reasoning_tokens is not None:
                metadata["reasoning_tokens"] = self.token_usage.reasoning_tokens
        if self.cost_info:
            metadata.update(
                {
                    "cost_total": self.cost_info.total_cost,
                    "cost_currency": self.cost_info.currency,
                }
            )
            if self.cost_info.prompt_cost is not None:
                metadata["cost_prompt"] = self.cost_info.prompt_cost
            if self.cost_info.completion_cost is not None:
                metadata["cost_completion"] = self.cost_info.completion_cost
        if self.error:
            metadata["error"] = self.error
        return metadata


__all__ = [
    "APICallMetadata",
    "CostInfo",
    "TokenUsage",
]
