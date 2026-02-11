"""Chat-oriented OpenRouter effects."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

from doeff_llm.effects import LLMChat, LLMStreamingChat


@dataclass(frozen=True, kw_only=True)
class RouterChat(LLMChat):
    """Deprecated alias of :class:`doeff_llm.effects.LLMChat`."""

    def __post_init__(self) -> None:
        warnings.warn(
            "RouterChat is deprecated; use doeff_llm.effects.LLMChat instead.",
            DeprecationWarning,
            stacklevel=2,
        )


@dataclass(frozen=True, kw_only=True)
class RouterStreamingChat(LLMStreamingChat):
    """Deprecated alias of :class:`doeff_llm.effects.LLMStreamingChat`."""

    def __post_init__(self) -> None:
        warnings.warn(
            "RouterStreamingChat is deprecated; use doeff_llm.effects.LLMStreamingChat instead.",
            DeprecationWarning,
            stacklevel=2,
        )


__all__ = [
    "RouterChat",
    "RouterStreamingChat",
]
