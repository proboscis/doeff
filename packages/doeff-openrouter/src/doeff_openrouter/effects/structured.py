"""Structured-output OpenRouter effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class RouterStructuredOutput(EffectBase):
    """Request structured output via OpenRouter."""

    messages: list[dict[str, Any]]
    response_format: type[Any]
    model: str


__all__ = ["RouterStructuredOutput"]
