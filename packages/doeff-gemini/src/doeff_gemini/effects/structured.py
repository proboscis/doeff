"""Structured output effects for doeff-gemini."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

from doeff_llm.effects import LLMStructuredQuery


@dataclass(frozen=True, kw_only=True)
class GeminiStructuredOutput(LLMStructuredQuery):
    """Deprecated alias of :class:`doeff_llm.effects.LLMStructuredQuery`."""

    def __post_init__(self) -> None:
        warnings.warn(
            "GeminiStructuredOutput is deprecated; use doeff_llm.effects.LLMStructuredQuery instead.",
            DeprecationWarning,
            stacklevel=2,
        )


__all__ = ["GeminiStructuredOutput"]
