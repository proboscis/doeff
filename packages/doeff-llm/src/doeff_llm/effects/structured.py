"""Provider-agnostic structured-output effects."""

from typing import Any

from doeff import EffectBase


class LLMStructuredQuery(EffectBase):
    """Request provider-agnostic structured output."""

    def __init__(
        self, *,
        messages: list[dict[str, Any]],
        response_format: type[Any],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ):
        super().__init__()
        self.messages = messages
        self.response_format = response_format
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.extra = extra or {}

    def __repr__(self):
        return f"LLMStructuredQuery(model={self.model!r}, format={self.response_format.__name__})"


__all__ = [
    "LLMStructuredQuery",
]
