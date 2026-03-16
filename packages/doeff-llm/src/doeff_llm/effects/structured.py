"""Provider-agnostic structured-output effects."""


from dataclasses import dataclass, field
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
    extra: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "LLMStructuredQuery",
]
