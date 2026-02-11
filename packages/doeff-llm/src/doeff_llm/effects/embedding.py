"""Provider-agnostic embedding effects."""

from __future__ import annotations

from dataclasses import dataclass

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class LLMEmbedding(EffectBase):
    """Request provider-agnostic embeddings."""

    input: str | list[str]
    model: str


__all__ = [
    "LLMEmbedding",
]
