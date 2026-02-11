"""Production handlers for doeff-openai domain effects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doeff import Delegate, Resume
from doeff_openai.chat import chat_completion
from doeff_openai.effects import (
    ChatCompletion,
    Embedding,
    StreamingChatCompletion,
    StructuredOutput,
)
from doeff_openai.embeddings import create_embedding
from doeff_openai.structured_llm import build_api_parameters, process_structured_response

ProtocolHandler = Callable[[Any, Any], Any]

DEFAULT_STRUCTURED_MAX_TOKENS = 8192
DEFAULT_STRUCTURED_TEMPERATURE = 0.7


def _handle_chat_completion(effect: ChatCompletion, k):
    response = yield chat_completion(
        messages=effect.messages,
        model=effect.model,
        temperature=effect.temperature,
        max_tokens=effect.max_tokens,
    )
    return (yield Resume(k, response))


def _handle_streaming_chat_completion(effect: StreamingChatCompletion, k):
    response = yield chat_completion(
        messages=effect.messages,
        model=effect.model,
        temperature=effect.temperature,
        stream=True,
    )
    return (yield Resume(k, response))


def _handle_embedding(effect: Embedding, k):
    response = yield create_embedding(
        input=effect.input,
        model=effect.model,
    )
    return (yield Resume(k, response))


def _handle_structured_output(effect: StructuredOutput, k):
    api_params = yield build_api_parameters(
        model=effect.model,
        messages=effect.messages,
        temperature=DEFAULT_STRUCTURED_TEMPERATURE,
        max_tokens=DEFAULT_STRUCTURED_MAX_TOKENS,
        reasoning_effort=None,
        verbosity=None,
        service_tier=None,
        response_format=effect.response_format,
    )
    response = yield chat_completion(**api_params)
    parsed = yield process_structured_response(response, effect.response_format)
    return (yield Resume(k, parsed))


def openai_production_handler(effect: Any, k: Any):
    """Single protocol handler suitable for ``WithHandler`` usage."""
    if isinstance(effect, ChatCompletion):
        return (yield from _handle_chat_completion(effect, k))
    if isinstance(effect, StreamingChatCompletion):
        return (yield from _handle_streaming_chat_completion(effect, k))
    if isinstance(effect, Embedding):
        return (yield from _handle_embedding(effect, k))
    if isinstance(effect, StructuredOutput):
        return (yield from _handle_structured_output(effect, k))
    yield Delegate()


def production_handlers() -> dict[type[Any], ProtocolHandler]:
    """Typed handler map for real OpenAI API execution."""
    return {
        ChatCompletion: _handle_chat_completion,
        StreamingChatCompletion: _handle_streaming_chat_completion,
        Embedding: _handle_embedding,
        StructuredOutput: _handle_structured_output,
    }


__all__ = [
    "DEFAULT_STRUCTURED_MAX_TOKENS",
    "DEFAULT_STRUCTURED_TEMPERATURE",
    "ProtocolHandler",
    "openai_production_handler",
    "production_handlers",
]
