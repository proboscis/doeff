"""Mock handlers for doeff-gemini domain effects."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, get_args, get_origin

from doeff_image.effects import ImageEdit, ImageGenerate
from doeff_image.types import ImageResult
from doeff_llm.effects import (
    LLMChat,
    LLMEmbedding,
    LLMStreamingChat,
    LLMStructuredQuery,
)
from PIL import Image as PILImage

from doeff import Pass, Resume
from doeff_gemini.effects import (
    GeminiChat,
    GeminiEmbedding,
    GeminiImageEdit,
    GeminiStreamingChat,
    GeminiStructuredOutput,
)
from doeff_gemini.handlers.production import _is_gemini_model

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


def _color_from_digest(seed_text: str) -> tuple[int, int, int]:
    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    return digest[0], digest[1], digest[2]


@dataclass
class MockGeminiHandler:
    """In-memory deterministic mock for Gemini effects."""

    default_chat_response: str = "mock-gemini-chat-response"
    chat_responses: Mapping[str, str] = field(default_factory=dict)
    structured_responses: Mapping[type[Any], Any] = field(default_factory=dict)
    embedding_responses: Mapping[str, list[float] | list[list[float]]] = field(default_factory=dict)
    image_generate_responses: Mapping[str, ImageResult] = field(default_factory=dict)
    image_edit_responses: Mapping[str, ImageResult] = field(default_factory=dict)
    embedding_dimensions: int = 8
    embedding_seed: int = 0

    def handle_chat(self, effect: LLMChat | LLMStreamingChat) -> str:
        configured = self.chat_responses.get(effect.model)
        if configured is not None:
            return configured
        signature = _message_signature(effect.messages)
        return f"{self.default_chat_response}:{signature}"

    def handle_structured(self, effect: LLMStructuredQuery) -> Any:
        configured = self.structured_responses.get(effect.response_format)
        if configured is None:
            configured = self._build_default_structured_payload(effect.response_format)
        return self._coerce_structured_response(effect.response_format, configured)

    def handle_embedding(self, effect: LLMEmbedding) -> list[float] | list[list[float]]:
        configured = self.embedding_responses.get(effect.model)
        if configured is not None:
            return configured

        if self.embedding_dimensions <= 0:
            raise ValueError("embedding_dimensions must be > 0")

        if isinstance(effect.input, str):
            return self._vector_for_text(effect.model, effect.input)
        return [self._vector_for_text(effect.model, text) for text in effect.input]

    def handle_image_generate(self, effect: ImageGenerate) -> ImageResult:
        configured = self.image_generate_responses.get(effect.model)
        if configured is not None:
            return configured
        return self._default_image_result(prompt=effect.prompt, model=effect.model)

    def handle_image_edit(self, effect: ImageEdit) -> ImageResult:
        configured = self.image_edit_responses.get(effect.model)
        if configured is not None:
            return configured
        return self._default_image_result(prompt=effect.prompt, model=effect.model)

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
            chunk = digest[offset : offset + 4]
            if len(chunk) < 4:
                chunk += digest[: 4 - len(chunk)]
            value = int.from_bytes(chunk, "big") / 0xFFFFFFFF
            vector.append(round(value, 6))

        return vector

    def _default_image_result(self, *, prompt: str, model: str) -> ImageResult:
        image = PILImage.new("RGB", (16, 16), _color_from_digest(f"{model}:{prompt}"))
        return ImageResult(
            images=[image],
            prompt=prompt,
            model=model,
            raw_response={"mock": True, "model": model},
        )


def mock_handlers(
    *,
    handler: MockGeminiHandler | None = None,
    default_chat_response: str = "mock-gemini-chat-response",
    chat_responses: Mapping[str, str] | None = None,
    structured_responses: Mapping[type[Any], Any] | None = None,
    embedding_responses: Mapping[str, list[float] | list[list[float]]] | None = None,
    image_generate_responses: Mapping[str, ImageResult] | None = None,
    image_edit_responses: Mapping[str, ImageResult] | None = None,
    embedding_dimensions: int = 8,
    embedding_seed: int = 0,
) -> dict[type[Any], ProtocolHandler]:
    """Build deterministic mock handlers for Gemini domain effects."""

    active_handler = handler or MockGeminiHandler(
        default_chat_response=default_chat_response,
        chat_responses=chat_responses or {},
        structured_responses=structured_responses or {},
        embedding_responses=embedding_responses or {},
        image_generate_responses=image_generate_responses or {},
        image_edit_responses=image_edit_responses or {},
        embedding_dimensions=embedding_dimensions,
        embedding_seed=embedding_seed,
    )

    def handle_chat(effect: LLMChat | GeminiChat, k):
        if not _is_gemini_model(effect.model):
            yield Pass()
            return
        return (yield Resume(k, active_handler.handle_chat(effect)))

    def handle_streaming_chat(effect: LLMStreamingChat | GeminiStreamingChat, k):
        if not _is_gemini_model(effect.model):
            yield Pass()
            return
        return (yield Resume(k, active_handler.handle_chat(effect)))

    def handle_structured(effect: LLMStructuredQuery | GeminiStructuredOutput, k):
        if not _is_gemini_model(effect.model):
            yield Pass()
            return
        return (yield Resume(k, active_handler.handle_structured(effect)))

    def handle_embedding(effect: LLMEmbedding | GeminiEmbedding, k):
        if not _is_gemini_model(effect.model):
            yield Pass()
            return
        return (yield Resume(k, active_handler.handle_embedding(effect)))

    def handle_image_generate(effect: ImageGenerate, k):
        return (yield Resume(k, active_handler.handle_image_generate(effect)))

    def handle_image_edit(effect: ImageEdit, k):
        return (yield Resume(k, active_handler.handle_image_edit(effect)))

    return {
        GeminiChat: handle_chat,
        GeminiStreamingChat: handle_streaming_chat,
        GeminiStructuredOutput: handle_structured,
        GeminiEmbedding: handle_embedding,
        LLMChat: handle_chat,
        LLMStreamingChat: handle_streaming_chat,
        LLMStructuredQuery: handle_structured,
        LLMEmbedding: handle_embedding,
        ImageGenerate: handle_image_generate,
        ImageEdit: handle_image_edit,
        GeminiImageEdit: handle_image_edit,
    }


def gemini_mock_handler(
    effect: Any,
    k: Any,
    *,
    handler: MockGeminiHandler | None = None,
    default_chat_response: str = "mock-gemini-chat-response",
    chat_responses: Mapping[str, str] | None = None,
    structured_responses: Mapping[type[Any], Any] | None = None,
    embedding_responses: Mapping[str, list[float] | list[list[float]]] | None = None,
    embedding_dimensions: int = 8,
    embedding_seed: int = 0,
):
    """Single protocol handler suitable for ``WithHandler`` usage."""
    active_handler = handler or MockGeminiHandler(
        default_chat_response=default_chat_response,
        chat_responses=chat_responses or {},
        structured_responses=structured_responses or {},
        embedding_responses=embedding_responses or {},
        embedding_dimensions=embedding_dimensions,
        embedding_seed=embedding_seed,
    )

    if isinstance(effect, LLMStreamingChat | GeminiStreamingChat | LLMChat | GeminiChat) and (
        _is_gemini_model(effect.model)
    ):
        return (yield Resume(k, active_handler.handle_chat(effect)))
    if isinstance(effect, LLMStructuredQuery | GeminiStructuredOutput) and _is_gemini_model(
        effect.model
    ):
        return (yield Resume(k, active_handler.handle_structured(effect)))
    if isinstance(effect, LLMEmbedding | GeminiEmbedding) and _is_gemini_model(effect.model):
        return (yield Resume(k, active_handler.handle_embedding(effect)))
    yield Pass()


__all__ = [
    "MockGeminiHandler",
    "ProtocolHandler",
    "gemini_mock_handler",
    "mock_handlers",
]
