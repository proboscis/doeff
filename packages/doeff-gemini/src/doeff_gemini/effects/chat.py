"""Chat domain effects for doeff-gemini."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

from doeff_llm.effects import LLMChat, LLMStreamingChat


@dataclass(frozen=True, kw_only=True)
class GeminiChat(LLMChat):
    """Deprecated alias of :class:`doeff_llm.effects.LLMChat`."""

    def __post_init__(self) -> None:
        warnings.warn(
            "GeminiChat is deprecated; use doeff_llm.effects.LLMChat instead.",
            DeprecationWarning,
            stacklevel=2,
        )


@dataclass(frozen=True, kw_only=True)
class GeminiStreamingChat(LLMStreamingChat):
    """Deprecated alias of :class:`doeff_llm.effects.LLMStreamingChat`."""

    def __post_init__(self) -> None:
        warnings.warn(
            "GeminiStreamingChat is deprecated; use doeff_llm.effects.LLMStreamingChat instead.",
            DeprecationWarning,
            stacklevel=2,
        )


__all__ = [
    "GeminiChat",
    "GeminiStreamingChat",
]
