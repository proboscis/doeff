"""Structured output effects for doeff-gemini."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class GeminiStructuredOutput(EffectBase):
    """Request structured output from Gemini."""

    messages: list[dict[str, Any]]
    response_format: type[Any]
    model: str


__all__ = ["GeminiStructuredOutput"]
