"""Provider-agnostic chat effects."""


from dataclasses import dataclass
from typing import Any

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class LLMChat(EffectBase):
    """Request a provider-agnostic chat completion."""

    messages: list[dict[str, Any]]
    model: str
    temperature: float = 0.7
    max_tokens: int | None = None
    stream: bool = False
    tools: list[dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class LLMStreamingChat(LLMChat):
    """Request a provider-agnostic streaming chat completion."""

    stream: bool = True


__all__ = [
    "LLMChat",
    "LLMStreamingChat",
]
