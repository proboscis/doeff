"""Common LLM-oriented type aliases and dataclasses."""


from dataclasses import dataclass
from typing import Any

Message = dict[str, Any]


@dataclass(frozen=True)
class TokenUsage:
    """Token usage snapshot for one LLM call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @property
    def input_tokens(self) -> int:
        """Alias for provider-neutral naming."""
        return self.prompt_tokens

    @property
    def output_tokens(self) -> int:
        """Alias for provider-neutral naming."""
        return self.completion_tokens


@dataclass(frozen=True)
class CostInfo:
    """Cost metadata for one LLM call."""

    total_cost: float
    currency: str = "USD"


__all__ = [
    "CostInfo",
    "Message",
    "TokenUsage",
]
