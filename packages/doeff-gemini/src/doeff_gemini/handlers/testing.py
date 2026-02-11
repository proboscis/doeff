"""Mock handlers for doeff-gemini domain effects."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, get_args, get_origin

from doeff import Resume
from doeff_gemini.effects import (
    GeminiChat,
    GeminiEmbedding,
    GeminiStreamingChat,
    GeminiStructuredOutput,
)

ProtocolHandler = Callable[[Any, Any], Any]


def _default_value_for_annotation(annotation: Any, field_name: str) -> Any:  # noqa: PLR0911
    if annotation in (str, Any):
        return f"mock-{field_name}"
    if annotation is int:
        return 0
    if annotation is float:
        return 0.0
    if annotation is bool:
        return False

    origin = get_origin(annotation)
    if origin is not None:
        args = get_args(annotation)
        non_none_args = [arg for arg in args if arg is not type(None)]
        if len(non_none_args) == 1:
            return _default_value_for_annotation(non_none_args[0], field_name)

        if origin in (list, tuple, set, frozenset):
            return []
        if origin is dict:
            return {}

    return f"mock-{field_name}"


def _message_signature(messages: list[dict[str, Any]]) -> str:
    try:
        payload = json.dumps(messages, sort_keys=True, default=str)
    except TypeError:
        payload = str(messages)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


@dataclass
class MockGeminiHandler:
    """In-memory deterministic mock for Gemini effects."""

    default_chat_response: str = "mock-gemini-chat-response"
    chat_responses: Mapping[str, str] = field(default_factory=dict)
    structured_responses: Mapping[type[Any], Any] = field(default_factory=dict)
    embedding_responses: Mapping[str, list[float] | list[list[float]]] = field(default_factory=dict)
    embedding_dimensions: int = 8
    embedding_seed: int = 0

    def handle_chat(self, effect: GeminiChat | GeminiStreamingChat) -> str:
        configured = self.chat_responses.get(effect.model)
        if configured is not None:
            return configured
        signature = _message_signature(effect.messages)
        return f"{self.default_chat_response}:{signature}"

    def handle_structured(self, effect: GeminiStructuredOutput) -> Any:
        configured = self.structured_responses.get(effect.response_format)
        if configured is None:
            configured = self._build_default_structured_payload(effect.response_format)
        return self._coerce_structured_response(effect.response_format, configured)

    def handle_embedding(self, effect: GeminiEmbedding) -> list[float] | list[list[float]]:
        configured = self.embedding_responses.get(effect.model)
        if configured is not None:
            return configured

        if self.embedding_dimensions <= 0:
            raise ValueError("embedding_dimensions must be > 0")

        if isinstance(effect.input, str):
            return self._vector_for_text(effect.model, effect.input)
        return [self._vector_for_text(effect.model, text) for text in effect.input]

    def _coerce_structured_response(self, response_format: type[Any], value: Any) -> Any:
        if isinstance(value, response_format):
            return value
        validator = getattr(response_format, "model_validate", None)
        if callable(validator):
            return validator(value)
        return value

    def _build_default_structured_payload(self, response_format: type[Any]) -> dict[str, Any]:
        model_fields = getattr(response_format, "model_fields", None)
        if not isinstance(model_fields, dict):
            return {}

        payload: dict[str, Any] = {}
        for field_name, field_info in model_fields.items():
            annotation = getattr(field_info, "annotation", Any)
            payload[field_name] = _default_value_for_annotation(annotation, field_name)
        return payload

    def _vector_for_text(self, model: str, text: str) -> list[float]:
        seed_input = f"{self.embedding_seed}:{model}:{text}".encode()
        digest = hashlib.sha256(seed_input).digest()
        vector: list[float] = []

        for index in range(self.embedding_dimensions):
            offset = (index * 4) % len(digest)
            chunk = digest[offset:offset + 4]
            if len(chunk) < 4:
                chunk += digest[: 4 - len(chunk)]
            value = int.from_bytes(chunk, "big") / 0xFFFFFFFF
            vector.append(round(value, 6))

        return vector


def mock_handlers(
    *,
    handler: MockGeminiHandler | None = None,
    default_chat_response: str = "mock-gemini-chat-response",
    chat_responses: Mapping[str, str] | None = None,
    structured_responses: Mapping[type[Any], Any] | None = None,
    embedding_responses: Mapping[str, list[float] | list[list[float]]] | None = None,
    embedding_dimensions: int = 8,
    embedding_seed: int = 0,
) -> dict[type[Any], ProtocolHandler]:
    """Build deterministic mock handlers for Gemini domain effects."""

    active_handler = handler or MockGeminiHandler(
        default_chat_response=default_chat_response,
        chat_responses=chat_responses or {},
        structured_responses=structured_responses or {},
        embedding_responses=embedding_responses or {},
        embedding_dimensions=embedding_dimensions,
        embedding_seed=embedding_seed,
    )

    def handle_chat(effect: GeminiChat, k):
        return (yield Resume(k, active_handler.handle_chat(effect)))

    def handle_streaming_chat(effect: GeminiStreamingChat, k):
        return (yield Resume(k, active_handler.handle_chat(effect)))

    def handle_structured(effect: GeminiStructuredOutput, k):
        return (yield Resume(k, active_handler.handle_structured(effect)))

    def handle_embedding(effect: GeminiEmbedding, k):
        return (yield Resume(k, active_handler.handle_embedding(effect)))

    return {
        GeminiChat: handle_chat,
        GeminiStreamingChat: handle_streaming_chat,
        GeminiStructuredOutput: handle_structured,
        GeminiEmbedding: handle_embedding,
    }


__all__ = [
    "MockGeminiHandler",
    "ProtocolHandler",
    "mock_handlers",
]
