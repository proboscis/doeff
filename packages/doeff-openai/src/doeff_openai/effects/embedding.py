"""Domain effects for OpenAI embedding operations."""

from __future__ import annotations

from dataclasses import dataclass

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class Embedding(EffectBase):
    """Request text embeddings from OpenAI."""

    input: str | list[str]
    model: str = "text-embedding-3-small"


__all__ = [
    "Embedding",
]
