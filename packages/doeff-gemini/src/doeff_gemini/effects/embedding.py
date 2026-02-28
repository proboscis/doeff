"""Embedding effects for doeff-gemini."""


import warnings
from dataclasses import dataclass

from doeff_llm.effects import LLMEmbedding


@dataclass(frozen=True, kw_only=True)
class GeminiEmbedding(LLMEmbedding):
    """Deprecated alias of :class:`doeff_llm.effects.LLMEmbedding`."""

    def __post_init__(self) -> None:
        warnings.warn(
            "GeminiEmbedding is deprecated; use doeff_llm.effects.LLMEmbedding instead.",
            DeprecationWarning,
            stacklevel=2,
        )


__all__ = ["GeminiEmbedding"]
