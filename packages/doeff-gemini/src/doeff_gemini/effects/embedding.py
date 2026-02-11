"""Embedding effects for doeff-gemini."""

from __future__ import annotations

from dataclasses import dataclass

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class GeminiEmbedding(EffectBase):
    """Request embeddings from Gemini."""

    input: str | list[str]
    model: str


__all__ = ["GeminiEmbedding"]
