"""Chat-oriented OpenRouter effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class RouterChat(EffectBase):
    """Request a chat completion via OpenRouter."""

    messages: list[dict[str, Any]]
    model: str
    temperature: float = 0.7


@dataclass(frozen=True, kw_only=True)
class RouterStreamingChat(EffectBase):
    """Request a streaming chat completion via OpenRouter."""

    messages: list[dict[str, Any]]
    model: str


__all__ = [
    "RouterChat",
    "RouterStreamingChat",
]
