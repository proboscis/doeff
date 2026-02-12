"""Production handlers for doeff-openai domain effects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doeff_llm.effects import (
    LLMChat,
    LLMEmbedding,
    LLMStreamingChat,
    LLMStructuredOutput,
)

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
OPENAI_MODEL_PREFIXES = ("gpt-", "o1-", "o3-", "o4-", "text-embedding-")
OPENAI_MODEL_EXCLUSIONS = ("text-embedding-004", "embedding-001")


def _is_openai_model(model: str) -> bool:
    if model in OPENAI_MODEL_EXCLUSIONS:
        return False
    return any(model.startswith(prefix) for prefix in OPENAI_MODEL_PREFIXES)


def _handle_chat_completion(effect: LLMChat, k):
    response = yield chat_completion(
        messages=effect.messages,
        model=effect.model,
        temperature=effect.temperature,
        max_tokens=effect.max_tokens,
        stream=effect.stream,
        tools=effect.tools,
    )
    return (yield Resume(k, response))


def _handle_streaming_chat_completion(effect: LLMStreamingChat | LLMChat, k):
    response = yield chat_completion(
        messages=effect.messages,
        model=effect.model,
        temperature=effect.temperature,
        max_tokens=effect.max_tokens,
        stream=True,
        tools=effect.tools,
    )
    return (yield Resume(k, response))


def _handle_embedding(effect: LLMEmbedding, k):
    response = yield create_embedding(
        input=effect.input,
        model=effect.model,
    )
    return (yield Resume(k, response))


def _handle_structured_output(effect: LLMStructuredOutput, k):
    api_params = yield build_api_parameters(
        model=effect.model,
        messages=effect.messages,
        temperature=(
            effect.temperature if effect.temperature is not None else DEFAULT_STRUCTURED_TEMPERATURE
        ),
        max_tokens=(
            effect.max_tokens if effect.max_tokens is not None else DEFAULT_STRUCTURED_MAX_TOKENS
        ),
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
    if isinstance(effect, LLMStreamingChat | StreamingChatCompletion):
        if _is_openai_model(effect.model):
            return (yield from _handle_streaming_chat_completion(effect, k))
    elif isinstance(effect, LLMChat | ChatCompletion):
        if _is_openai_model(effect.model):
            if effect.stream:
                return (yield from _handle_streaming_chat_completion(effect, k))
            return (yield from _handle_chat_completion(effect, k))
    elif isinstance(effect, LLMEmbedding | Embedding) and _is_openai_model(effect.model):
        return (yield from _handle_embedding(effect, k))
    elif isinstance(effect, LLMStructuredOutput | StructuredOutput) and _is_openai_model(
        effect.model
    ):
        return (yield from _handle_structured_output(effect, k))
    yield Delegate()


def production_handlers() -> dict[type[Any], ProtocolHandler]:
    """Typed handler map for real OpenAI API execution."""
    return {
        ChatCompletion: _handle_chat_completion,
        StreamingChatCompletion: _handle_streaming_chat_completion,
        Embedding: _handle_embedding,
        StructuredOutput: _handle_structured_output,
        LLMChat: _handle_chat_completion,
        LLMStreamingChat: _handle_streaming_chat_completion,
        LLMEmbedding: _handle_embedding,
        LLMStructuredOutput: _handle_structured_output,
    }


__all__ = [
    "DEFAULT_STRUCTURED_MAX_TOKENS",
    "DEFAULT_STRUCTURED_TEMPERATURE",
    "OPENAI_MODEL_EXCLUSIONS",
    "OPENAI_MODEL_PREFIXES",
    "ProtocolHandler",
    "_is_openai_model",
    "openai_production_handler",
    "production_handlers",
]
