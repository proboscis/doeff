"""Mock handlers for doeff-gemini domain effects."""


import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, get_args, get_origin

from doeff_image import ImageEdit, ImageGenerate, ImageResult
from doeff_llm import LLMChat, LLMEmbedding, LLMStreamingChat, LLMStructuredQuery
from PIL import Image as PILImage

from doeff import Pass, Resume, do
from doeff import handler as _program_handler
from doeff_gemini._effects import (
    GeminiChat,
    GeminiEmbedding,
    GeminiImageEdit,
    GeminiStreamingChat,
    GeminiStructuredOutput,
)
from doeff_gemini._handlers.production import _is_gemini_model

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


@dataclass(frozen=True)
class _MockGemini:
    """What the Gemini mock handler answers with: the recorded answers by model or type, and the defaults' settings."""

    default_chat_response: str = "mock-gemini-chat-response"
    chat_responses: Mapping[str, str] = field(default_factory=dict)
    structured_responses: Mapping[type[Any], Any] = field(default_factory=dict)
    embedding_responses: Mapping[str, list[float] | list[list[float]]] = field(default_factory=dict)
    image_generate_responses: Mapping[str, ImageResult] = field(default_factory=dict)
    image_edit_responses: Mapping[str, ImageResult] = field(default_factory=dict)
    embedding_dimensions: int = 8
    embedding_seed: int = 0


def _chat_answer(mock: _MockGemini, effect: LLMChat | LLMStreamingChat) -> str:
    configured = mock.chat_responses.get(effect.model)
    if configured is not None:
        return configured
    signature = _message_signature(effect.messages)
    return f"{mock.default_chat_response}:{signature}"


def _structured_answer(mock: _MockGemini, effect: LLMStructuredQuery) -> Any:
    configured = mock.structured_responses.get(effect.response_format)
    if configured is None:
        configured = _build_default_structured_payload(effect.response_format)
    return _coerce_structured_response(effect.response_format, configured)


def _embedding_answer(mock: _MockGemini, effect: LLMEmbedding) -> list[float] | list[list[float]]:
    configured = mock.embedding_responses.get(effect.model)
    if configured is not None:
        return configured

    if mock.embedding_dimensions <= 0:
        raise ValueError("embedding_dimensions must be > 0")

    if isinstance(effect.input, str):
        return _vector_for_text(mock, effect.model, effect.input)
    return [_vector_for_text(mock, effect.model, text) for text in effect.input]


def _image_generate_answer(mock: _MockGemini, effect: ImageGenerate) -> ImageResult:
    configured = mock.image_generate_responses.get(effect.model)
    if configured is not None:
        return configured
    return _default_image_result(prompt=effect.prompt, model=effect.model)


def _image_edit_answer(mock: _MockGemini, effect: ImageEdit) -> ImageResult:
    configured = mock.image_edit_responses.get(effect.model)
    if configured is not None:
        return configured
    return _default_image_result(prompt=effect.prompt, model=effect.model)


def _coerce_structured_response(response_format: type[Any], value: Any) -> Any:
    if isinstance(value, response_format):
        return value
    validator = getattr(response_format, "model_validate", None)
    if callable(validator):
        return validator(value)
    return value


def _build_default_structured_payload(response_format: type[Any]) -> dict[str, Any]:
    model_fields = getattr(response_format, "model_fields", None)
    if not isinstance(model_fields, dict):
        return {}

    payload: dict[str, Any] = {}
    for field_name, field_info in model_fields.items():
        annotation = getattr(field_info, "annotation", Any)
        payload[field_name] = _default_value_for_annotation(annotation, field_name)
    return payload


def _vector_for_text(mock: _MockGemini, model: str, text: str) -> list[float]:
    seed_input = f"{mock.embedding_seed}:{model}:{text}".encode()
    digest = hashlib.sha256(seed_input).digest()
    vector: list[float] = []

    for index in range(mock.embedding_dimensions):
        offset = (index * 4) % len(digest)
        chunk = digest[offset : offset + 4]
        if len(chunk) < 4:
            chunk += digest[: 4 - len(chunk)]
        value = int.from_bytes(chunk, "big") / 0xFFFFFFFF
        vector.append(round(value, 6))

    return vector


def _default_image_result(*, prompt: str, model: str) -> ImageResult:
    image = PILImage.new("RGB", (16, 16), _color_from_digest(f"{model}:{prompt}"))
    return ImageResult(
        images=[image],
        prompt=prompt,
        model=model,
        raw_response={"mock": True, "model": model},
    )


def mock_handlers(
    *,
    default_chat_response: str = "mock-gemini-chat-response",
    chat_responses: Mapping[str, str] | None = None,
    structured_responses: Mapping[type[Any], Any] | None = None,
    embedding_responses: Mapping[str, list[float] | list[list[float]]] | None = None,
    image_generate_responses: Mapping[str, ImageResult] | None = None,
    image_edit_responses: Mapping[str, ImageResult] | None = None,
    embedding_dimensions: int = 8,
    embedding_seed: int = 0,
) -> ProtocolHandler:
    """Build a deterministic mock protocol handler for Gemini domain effects."""

    mock = _MockGemini(
        default_chat_response=default_chat_response,
        chat_responses=chat_responses or {},
        structured_responses=structured_responses or {},
        embedding_responses=embedding_responses or {},
        image_generate_responses=image_generate_responses or {},
        image_edit_responses=image_edit_responses or {},
        embedding_dimensions=embedding_dimensions,
        embedding_seed=embedding_seed,
    )

    @do
    def handler(effect: Any, k: Any):  # noqa: PLR0911 - baseline cleanup keeps existing control flow unchanged
        if isinstance(effect, LLMStreamingChat | GeminiStreamingChat):
            if not _is_gemini_model(effect.model):
                yield Pass(effect, k)
                return None
            return (yield Resume(k, _chat_answer(mock, effect)))
        if isinstance(effect, LLMChat | GeminiChat):
            if not _is_gemini_model(effect.model):
                yield Pass(effect, k)
                return None
            return (yield Resume(k, _chat_answer(mock, effect)))
        if isinstance(effect, LLMStructuredQuery | GeminiStructuredOutput):
            if not _is_gemini_model(effect.model):
                yield Pass(effect, k)
                return None
            return (yield Resume(k, _structured_answer(mock, effect)))
        if isinstance(effect, LLMEmbedding | GeminiEmbedding):
            if not _is_gemini_model(effect.model):
                yield Pass(effect, k)
                return None
            return (yield Resume(k, _embedding_answer(mock, effect)))
        if isinstance(effect, ImageGenerate):
            return (yield Resume(k, _image_generate_answer(mock, effect)))
        if isinstance(effect, ImageEdit | GeminiImageEdit):
            return (yield Resume(k, _image_edit_answer(mock, effect)))
        yield Pass(effect, k)

    return _program_handler(handler)


@do
def gemini_mock_handler(
    effect: Any,
    k: Any,
    *,
    default_chat_response: str = "mock-gemini-chat-response",
    chat_responses: Mapping[str, str] | None = None,
    structured_responses: Mapping[type[Any], Any] | None = None,
    embedding_responses: Mapping[str, list[float] | list[list[float]]] | None = None,
    embedding_dimensions: int = 8,
    embedding_seed: int = 0,
):
    """Single protocol handler suitable for ``WithHandler`` usage."""
    mock = _MockGemini(
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
        return (yield Resume(k, _chat_answer(mock, effect)))
    if isinstance(effect, LLMStructuredQuery | GeminiStructuredOutput) and _is_gemini_model(
        effect.model
    ):
        return (yield Resume(k, _structured_answer(mock, effect)))
    if isinstance(effect, LLMEmbedding | GeminiEmbedding) and _is_gemini_model(effect.model):
        return (yield Resume(k, _embedding_answer(mock, effect)))
    yield Pass(effect, k)


__all__ = [
    "ProtocolHandler",
    "gemini_mock_handler",
    "mock_handlers",
]
