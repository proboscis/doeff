"""Structured-output OpenRouter effects."""


import warnings
from dataclasses import dataclass

from doeff_llm.effects import LLMStructuredQuery


@dataclass(frozen=True, kw_only=True)
class RouterStructuredOutput(LLMStructuredQuery):
    """Deprecated alias of :class:`doeff_llm.effects.LLMStructuredQuery`."""

    def __post_init__(self) -> None:
        warnings.warn(
            "RouterStructuredOutput is deprecated; use doeff_llm.effects.LLMStructuredQuery instead.",
            DeprecationWarning,
            stacklevel=2,
        )


__all__ = ["RouterStructuredOutput"]
