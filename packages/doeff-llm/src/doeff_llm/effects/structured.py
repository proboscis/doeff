"""Provider-agnostic structured-output effects."""


from dataclasses import dataclass
from typing import Any

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class LLMStructuredQuery(EffectBase):
    """Request provider-agnostic structured output."""

    messages: list[dict[str, Any]]
    response_format: type[Any]
    model: str
    temperature: float = 0.7
    max_tokens: int | None = None


__all__ = [
    "LLMStructuredQuery",
]
