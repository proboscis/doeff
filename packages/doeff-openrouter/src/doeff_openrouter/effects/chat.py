"""Chat-oriented OpenRouter effects."""


import warnings

from doeff_llm.effects import LLMChat, LLMStreamingChat


class RouterChat(LLMChat):
    """Deprecated alias of :class:`doeff_llm.effects.LLMChat`."""

    def __init__(self, **kwargs):
        # The doeff_llm base effects define an explicit ``__init__`` (they
        # are not dataclasses); forward to it so the alias keeps the base
        # constructor signature.
        super().__init__(**kwargs)
        warnings.warn(
            "RouterChat is deprecated; use doeff_llm.effects.LLMChat instead.",
            DeprecationWarning,
            stacklevel=2,
        )


class RouterStreamingChat(LLMStreamingChat):
    """Deprecated alias of :class:`doeff_llm.effects.LLMStreamingChat`."""

    def __init__(self, **kwargs):
        # The doeff_llm base effects define an explicit ``__init__`` (they
        # are not dataclasses); forward to it so the alias keeps the base
        # constructor signature.
        super().__init__(**kwargs)
        warnings.warn(
            "RouterStreamingChat is deprecated; use doeff_llm.effects.LLMStreamingChat instead.",
            DeprecationWarning,
            stacklevel=2,
        )
