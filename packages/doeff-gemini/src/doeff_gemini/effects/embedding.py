"""Embedding effects for doeff-gemini."""


import warnings

from doeff_llm.effects import LLMEmbedding


class GeminiEmbedding(LLMEmbedding):
    """Deprecated alias of :class:`doeff_llm.effects.LLMEmbedding`."""

    def __init__(self, **kwargs):
        # The doeff_llm base effects define an explicit ``__init__`` (they
        # are not dataclasses); forward to it so the alias keeps the base
        # constructor signature.
        super().__init__(**kwargs)
        warnings.warn(
            "GeminiEmbedding is deprecated; use doeff_llm.effects.LLMEmbedding instead.",
            DeprecationWarning,
            stacklevel=2,
        )
