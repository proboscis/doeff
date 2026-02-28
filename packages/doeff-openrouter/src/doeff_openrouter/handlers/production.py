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

from doeff import Pass, Resume, do
from doeff.effects.base import Effect
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


@do
def _handle_chat(effect: LLMChat, k):
    response = yield chat_completion(
        messages=effect.messages,
        model=effect.model,
        temperature=effect.temperature,
        max_tokens=effect.max_tokens,
        tools=effect.tools,
    )
    return (yield Resume(k, response))


@do
def _handle_streaming_chat(effect: LLMStreamingChat | LLMChat, k):
    response = yield chat_completion(
        messages=effect.messages,
        model=effect.model,
        max_tokens=effect.max_tokens,
        stream=True,
        tools=effect.tools,
    )
    return (yield Resume(k, response))


@do
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


@do
def openrouter_production_handler(effect: Effect, k: Any):
    """Single protocol handler suitable for ``WithHandler`` usage."""
    if isinstance(effect, LLMStreamingChat | RouterStreamingChat):
        return (yield _handle_streaming_chat(effect, k))
    if isinstance(effect, LLMChat | RouterChat):
        if effect.stream:
            return (yield _handle_streaming_chat(effect, k))
        return (yield _handle_chat(effect, k))
    if isinstance(effect, LLMStructuredQuery | RouterStructuredOutput):
        return (yield _handle_structured_output(effect, k))
    if isinstance(effect, LLMEmbedding):
        yield Pass()
        return
    yield Pass()


def production_handlers() -> ProtocolHandler:
    """Build a protocol handler backed by the real OpenRouter client helpers."""
    return openrouter_production_handler


__all__ = [
    "ProtocolHandler",
    "openrouter_production_handler",
    "production_handlers",
]
