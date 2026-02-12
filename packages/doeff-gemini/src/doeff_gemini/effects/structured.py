"""Structured output effects for doeff-gemini."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

from doeff_llm.effects import LLMStructuredOutput


@dataclass(frozen=True, kw_only=True)
class GeminiStructuredOutput(LLMStructuredOutput):
    """Deprecated alias of :class:`doeff_llm.effects.LLMStructuredOutput`."""

    def __post_init__(self) -> None:
        warnings.warn(
            "GeminiStructuredOutput is deprecated; use doeff_llm.effects.LLMStructuredOutput instead.",
            DeprecationWarning,
            stacklevel=2,
        )


__all__ = ["GeminiStructuredOutput"]
