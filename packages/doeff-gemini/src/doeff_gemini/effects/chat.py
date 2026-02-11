"""Chat domain effects for doeff-gemini."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class GeminiChat(EffectBase):
    """Request a chat completion from Gemini."""

    messages: list[dict[str, Any]]
    model: str
    temperature: float = 0.7


@dataclass(frozen=True, kw_only=True)
class GeminiStreamingChat(EffectBase):
    """Request a streaming chat completion from Gemini."""

    messages: list[dict[str, Any]]
    model: str
    temperature: float = 0.7


__all__ = [
    "GeminiChat",
    "GeminiStreamingChat",
]
