"""Provider-agnostic chat effects."""

from typing import Any

from doeff import EffectBase


class LLMChat(EffectBase):
    """Request a provider-agnostic chat completion."""

    def __init__(
        self, *,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ):
        super().__init__()
        self.messages = messages
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.stream = stream
        self.tools = tools

    def __repr__(self):
        return f"LLMChat(model={self.model!r}, messages=[{len(self.messages)} msgs])"


class LLMStreamingChat(LLMChat):
    """Request a provider-agnostic streaming chat completion."""

    def __init__(self, **kwargs):
        kwargs["stream"] = True
        super().__init__(**kwargs)


__all__ = [
    "LLMChat",
    "LLMStreamingChat",
]
