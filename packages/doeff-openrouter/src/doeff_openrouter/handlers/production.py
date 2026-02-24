"""Production handlers for doeff-openrouter effects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doeff_llm.effects import (
    LLMChat,
    LLMEmbedding,
    LLMStreamingChat,
    LLMStructuredQuery,
)

from doeff import Delegate, Resume
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
        raise TypeError("RouterStructuredOutput.response_format must be a Pydantic model class")


def _handle_chat(effect: LLMChat, k):
    response = yield chat_completion(
        messages=effect.messages,
        model=effect.model,
        temperature=effect.temperature,
        max_tokens=effect.max_tokens,
        tools=effect.tools,
    )
    return (yield Resume(k, response))


def _handle_streaming_chat(effect: LLMStreamingChat | LLMChat, k):
    response = yield chat_completion(
        messages=effect.messages,
        model=effect.model,
        max_tokens=effect.max_tokens,
        stream=True,
        tools=effect.tools,
    )
    return (yield Resume(k, response))


def _handle_structured_output(effect: LLMStructuredQuery, k):
    _validate_response_format(effect.response_format)
    response_format_payload = build_response_format_payload(effect.response_format)
    raw_response = yield chat_completion(
        messages=effect.messages,
        model=effect.model,
        temperature=effect.temperature,
        max_tokens=effect.max_tokens,
        response_format=response_format_payload,
    )
    structured_response = yield process_structured_response(
        raw_response,
        effect.response_format,
    )
    return (yield Resume(k, structured_response))


def openrouter_production_handler(effect: Any, k: Any):
    """Single protocol handler suitable for ``WithHandler`` usage."""
    if isinstance(effect, LLMStreamingChat | RouterStreamingChat):
        return (yield from _handle_streaming_chat(effect, k))
    if isinstance(effect, LLMChat | RouterChat):
        if effect.stream:
            return (yield from _handle_streaming_chat(effect, k))
        return (yield from _handle_chat(effect, k))
    if isinstance(effect, LLMStructuredQuery | RouterStructuredOutput):
        return (yield from _handle_structured_output(effect, k))
    if isinstance(effect, LLMEmbedding):
        yield Delegate()
        return
    yield Delegate()


def production_handlers() -> dict[type[Any], ProtocolHandler]:
    """Build handler map backed by the real OpenRouter client helpers."""

    return {
        RouterChat: _handle_chat,
        RouterStreamingChat: _handle_streaming_chat,
        RouterStructuredOutput: _handle_structured_output,
        LLMChat: _handle_chat,
        LLMStreamingChat: _handle_streaming_chat,
        LLMStructuredQuery: _handle_structured_output,
    }


__all__ = [
    "ProtocolHandler",
    "openrouter_production_handler",
    "production_handlers",
]
