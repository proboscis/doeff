"""Domain effects for OpenAI structured output operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff import EffectBase


@dataclass(frozen=True, kw_only=True)
class StructuredOutput(EffectBase):
    """Request structured output from OpenAI."""

    messages: list[dict[str, Any]]
    response_format: type[Any]
    model: str


__all__ = [
    "StructuredOutput",
]
