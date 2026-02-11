"""Production handlers for doeff-openrouter effects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doeff import Resume
from doeff_openrouter.chat import chat_completion
from doeff_openrouter.effects import (
    RouterChat,
    RouterStreamingChat,
    RouterStructuredOutput,
)
from doeff_openrouter.structured_llm import (
    build_response_format_payload,
    process_structured_response,
)

ProtocolHandler = Callable[[Any, Any], Any]


def _validate_response_format(response_format: type[Any]) -> None:
    if not isinstance(response_format, type):
        raise TypeError("RouterStructuredOutput.response_format must be a type")
    if not hasattr(response_format, "model_json_schema"):
        raise TypeError(
            "RouterStructuredOutput.response_format must be a Pydantic model class"
        )


def production_handlers() -> dict[type[Any], ProtocolHandler]:
    """Build handler map backed by the real OpenRouter client helpers."""

    def handle_chat(effect: RouterChat, k):
        response = yield chat_completion(
            messages=effect.messages,
            model=effect.model,
            temperature=effect.temperature,
        )
        return (yield Resume(k, response))

    def handle_streaming_chat(effect: RouterStreamingChat, k):
        response = yield chat_completion(
            messages=effect.messages,
            model=effect.model,
            stream=True,
        )
        return (yield Resume(k, response))

    def handle_structured_output(effect: RouterStructuredOutput, k):
        _validate_response_format(effect.response_format)
        response_format_payload = build_response_format_payload(effect.response_format)
        raw_response = yield chat_completion(
            messages=effect.messages,
            model=effect.model,
            response_format=response_format_payload,
        )
        structured_response = yield process_structured_response(
            raw_response,
            effect.response_format,
        )
        return (yield Resume(k, structured_response))

    return {
        RouterChat: handle_chat,
        RouterStreamingChat: handle_streaming_chat,
        RouterStructuredOutput: handle_structured_output,
    }


__all__ = [
    "ProtocolHandler",
    "production_handlers",
]
