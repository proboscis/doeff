"""Domain effects for OpenAI chat completion operations."""


import warnings

from doeff_llm import LLMChat, LLMStreamingChat


class ChatCompletion(LLMChat):
    """Deprecated alias of :class:`doeff_llm._effects.LLMChat`.

    ``LLMChat`` defines an explicit ``__init__`` rather than being a
    ``@dataclass``, so subclassing with ``@dataclass(kw_only=True)`` would
    silently replace the parent's constructor with a zero-field one and
    break every caller. Forwarding via ``__init__`` preserves the base
    signature while keeping the deprecation surface.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        warnings.warn(
            "ChatCompletion is deprecated; use doeff_llm._effects.LLMChat instead.",
            DeprecationWarning,
            stacklevel=2,
        )


class StreamingChatCompletion(LLMStreamingChat):
    """Deprecated alias of :class:`doeff_llm._effects.LLMStreamingChat`."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        warnings.warn(
            "StreamingChatCompletion is deprecated; use doeff_llm._effects.LLMStreamingChat instead.",
            DeprecationWarning,
            stacklevel=2,
        )


__all__ = [
    "ChatCompletion",
    "StreamingChatCompletion",
]
