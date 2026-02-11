"""Production handlers for doeff-gemini domain effects."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from doeff import Await, EffectGenerator, Resume, Safe, do
from doeff_gemini.client import get_gemini_client, track_api_call
from doeff_gemini.effects import (
    GeminiChat,
    GeminiEmbedding,
    GeminiStreamingChat,
    GeminiStructuredOutput,
)
from doeff_gemini.structured_llm import structured_llm__gemini

ProtocolHandler = Callable[[Any, Any], Any]


def _content_to_text(content: Any) -> str:  # noqa: PLR0911
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        try:
            return json.dumps(content)
        except TypeError:
            return str(content)
    if isinstance(content, list):
        parts = [_content_to_text(part) for part in content]
        return "\n".join(part for part in parts if part)
    return str(content)


def _messages_to_prompt(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = _content_to_text(message.get("content"))
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines).strip()


@do
def _chat_impl(effect: GeminiChat) -> EffectGenerator[str]:
    prompt = _messages_to_prompt(effect.messages)
    return (
        yield structured_llm__gemini(
            text=prompt,
            model=effect.model,
            temperature=effect.temperature,
            response_format=None,
        )
    )


@do
def _streaming_chat_impl(effect: GeminiStreamingChat) -> EffectGenerator[str]:
    prompt = _messages_to_prompt(effect.messages)
    return (
        yield structured_llm__gemini(
            text=prompt,
            model=effect.model,
            temperature=effect.temperature,
            response_format=None,
        )
    )


@do
def _structured_impl(effect: GeminiStructuredOutput) -> EffectGenerator[Any]:
    prompt = _messages_to_prompt(effect.messages)
    return (
        yield structured_llm__gemini(
            text=prompt,
            model=effect.model,
            response_format=effect.response_format,
        )
    )


def _extract_embedding_vectors(response: Any) -> list[list[float]]:  # noqa: PLR0912
    if response is None:
        raise ValueError("Gemini embedding response is missing")

    raw_embeddings = getattr(response, "embeddings", None)
    if raw_embeddings is None and isinstance(response, dict):
        raw_embeddings = response.get("embeddings")

    if raw_embeddings is None:
        single_embedding = getattr(response, "embedding", None)
        if single_embedding is None and isinstance(response, dict):
            single_embedding = response.get("embedding")
        if single_embedding is not None:
            raw_embeddings = [single_embedding]

    if raw_embeddings is None:
        raise ValueError("Gemini embedding response missing embeddings")

    vectors: list[list[float]] = []
    for embedding in raw_embeddings:
        values = getattr(embedding, "values", None)
        if values is None and isinstance(embedding, dict):
            values = embedding.get("values")

        if values is None:
            nested_embedding = getattr(embedding, "embedding", None)
            if nested_embedding is None and isinstance(embedding, dict):
                nested_embedding = embedding.get("embedding")
            if nested_embedding is not None:
                values = getattr(nested_embedding, "values", None)
                if values is None and isinstance(nested_embedding, dict):
                    values = nested_embedding.get("values")

        if values is None:
            continue

        vectors.append([float(value) for value in values])

    if not vectors:
        raise ValueError("Gemini embedding response missing vector values")

    return vectors


@do
def _embedding_impl(effect: GeminiEmbedding) -> EffectGenerator[list[float] | list[list[float]]]:
    client = yield get_gemini_client()
    async_client = client.async_client
    start_time = time.time()

    request_summary = {
        "operation": "embed_content",
        "model": effect.model,
        "input_type": "str" if isinstance(effect.input, str) else "list",
    }
    request_payload = {
        "text": effect.input if isinstance(effect.input, str) else None,
        "input": effect.input,
    }
    api_payload = {
        "model": effect.model,
        "contents": effect.input,
    }

    @do
    def api_call_with_tracking() -> EffectGenerator[Any]:
        response = yield Await(
            async_client.models.embed_content(
                model=effect.model,
                contents=effect.input,
            )
        )
        yield track_api_call(
            operation="embed_content",
            model=effect.model,
            request_summary=request_summary,
            request_payload=request_payload,
            response=response,
            start_time=start_time,
            error=None,
            api_payload=api_payload,
        )
        return response

    safe_result = yield Safe(api_call_with_tracking())
    if safe_result.is_err():
        exc = safe_result.error
        yield track_api_call(
            operation="embed_content",
            model=effect.model,
            request_summary=request_summary,
            request_payload=request_payload,
            response=None,
            start_time=start_time,
            error=exc,
            api_payload=api_payload,
        )
        raise exc

    vectors = _extract_embedding_vectors(safe_result.value)
    if isinstance(effect.input, str):
        return vectors[0]
    return vectors


def production_handlers(
    *,
    chat_impl: Callable[[GeminiChat], EffectGenerator[str]] | None = None,
    streaming_chat_impl: Callable[[GeminiStreamingChat], EffectGenerator[str]] | None = None,
    structured_impl: Callable[[GeminiStructuredOutput], EffectGenerator[Any]] | None = None,
    embedding_impl: Callable[
        [GeminiEmbedding],
        EffectGenerator[list[float] | list[list[float]]],
    ]
    | None = None,
) -> dict[type[Any], ProtocolHandler]:
    """Build effect handlers backed by real Gemini API integrations."""

    active_chat_impl = chat_impl or _chat_impl
    active_streaming_chat_impl = streaming_chat_impl or _streaming_chat_impl
    active_structured_impl = structured_impl or _structured_impl
    active_embedding_impl = embedding_impl or _embedding_impl

    def handle_chat(effect: GeminiChat, k):
        value = yield active_chat_impl(effect)
        return (yield Resume(k, value))

    def handle_streaming_chat(effect: GeminiStreamingChat, k):
        value = yield active_streaming_chat_impl(effect)
        return (yield Resume(k, value))

    def handle_structured(effect: GeminiStructuredOutput, k):
        value = yield active_structured_impl(effect)
        return (yield Resume(k, value))

    def handle_embedding(effect: GeminiEmbedding, k):
        value = yield active_embedding_impl(effect)
        return (yield Resume(k, value))

    return {
        GeminiChat: handle_chat,
        GeminiStreamingChat: handle_streaming_chat,
        GeminiStructuredOutput: handle_structured,
        GeminiEmbedding: handle_embedding,
    }


__all__ = [
    "ProtocolHandler",
    "production_handlers",
]
