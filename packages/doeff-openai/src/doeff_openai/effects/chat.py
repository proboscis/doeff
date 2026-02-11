"""Domain effects for OpenAI chat completion operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class ChatCompletion(EffectBase):
    """Request a chat completion from OpenAI."""

    messages: list[dict[str, Any]]
    model: str
    temperature: float = 0.7
    max_tokens: int | None = None


@dataclass(frozen=True, kw_only=True)
class StreamingChatCompletion(EffectBase):
    """Request a streaming chat completion from OpenAI."""

    messages: list[dict[str, Any]]
    model: str
    temperature: float = 0.7


__all__ = [
    "ChatCompletion",
    "StreamingChatCompletion",
]
