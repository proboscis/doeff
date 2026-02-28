"""Domain effects for OpenAI embedding operations."""


import warnings
from dataclasses import dataclass

from doeff_llm.effects import LLMEmbedding


@dataclass(frozen=True, kw_only=True)
class Embedding(LLMEmbedding):
    """Deprecated alias of :class:`doeff_llm.effects.LLMEmbedding`."""

    model: str = "text-embedding-3-small"

    def __post_init__(self) -> None:
        warnings.warn(
            "Embedding is deprecated; use doeff_llm.effects.LLMEmbedding instead.",
            DeprecationWarning,
            stacklevel=2,
        )


__all__ = [
    "Embedding",
]
