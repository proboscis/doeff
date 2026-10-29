"""Chat domain effects for doeff-gemini."""


import warnings

from doeff_llm.effects import LLMChat, LLMStreamingChat


class GeminiChat(LLMChat):
    """Deprecated alias of :class:`doeff_llm.effects.LLMChat`."""

    def __init__(self, **kwargs):
        # The doeff_llm base effects define an explicit ``__init__`` (they
        # are not dataclasses); forward to it so the alias keeps the base
        # constructor signature.
        super().__init__(**kwargs)
        warnings.warn(
            "GeminiChat is deprecated; use doeff_llm.effects.LLMChat instead.",
            DeprecationWarning,
            stacklevel=2,
        )


class GeminiStreamingChat(LLMStreamingChat):
    """Deprecated alias of :class:`doeff_llm.effects.LLMStreamingChat`."""

    def __init__(self, **kwargs):
        # The doeff_llm base effects define an explicit ``__init__`` (they
        # are not dataclasses); forward to it so the alias keeps the base
        # constructor signature.
        super().__init__(**kwargs)
        warnings.warn(
            "GeminiStreamingChat is deprecated; use doeff_llm.effects.LLMStreamingChat instead.",
            DeprecationWarning,
            stacklevel=2,
        )
