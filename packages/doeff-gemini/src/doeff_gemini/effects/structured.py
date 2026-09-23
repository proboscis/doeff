"""Structured output effects for doeff-gemini."""


import warnings

from doeff_llm.effects import LLMStructuredQuery


class GeminiStructuredOutput(LLMStructuredQuery):
    """Deprecated alias of :class:`doeff_llm.effects.LLMStructuredQuery`."""

    def __init__(self, **kwargs):
        # The doeff_llm base effects define an explicit ``__init__`` (they
        # are not dataclasses); forward to it so the alias keeps the base
        # constructor signature.
        super().__init__(**kwargs)
        warnings.warn(
            "GeminiStructuredOutput is deprecated; use doeff_llm.effects.LLMStructuredQuery instead.",
            DeprecationWarning,
            stacklevel=2,
        )
