"""Production handlers for doeff-gemini domain effects."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from io import BytesIO
from typing import Any

from doeff_image.effects import ImageEdit, ImageGenerate
from doeff_image.types import ImageResult
from doeff_llm.effects import (
    LLMChat,
    LLMEmbedding,
    LLMStreamingChat,
    LLMStructuredOutput,
)
from PIL import Image

from doeff import Await, Delegate, EffectGenerator, Resume, Try, do
from doeff_gemini.client import get_gemini_client, track_api_call
from doeff_gemini.effects import (
    GeminiChat,
    GeminiEmbedding,
    GeminiImageEdit,
    GeminiStreamingChat,
    GeminiStructuredOutput,
)
from doeff_gemini.structured_llm import edit_image__gemini, structured_llm__gemini

ProtocolHandler = Callable[[Any, Any], Any]
GEMINI_MODEL_PREFIXES = ("gemini-",)
GEMINI_MODEL_EXACT = ("text-embedding-004", "embedding-001")


def _is_gemini_model(model: str) -> bool:
    return model in GEMINI_MODEL_EXACT or any(
        model.startswith(prefix) for prefix in GEMINI_MODEL_PREFIXES
    )


GEMINI_IMAGE_MODEL_PREFIXES = (
    "gemini-3-pro-image",
    "gemini-3-pro-image-preview",
    "gemini-2.5-flash-image-preview",
    "gemini-2.0-flash-preview-image",
)
_ALLOWED_ASPECT_RATIOS = {
    "1:1",
    "2:3",
    "3:2",
    "3:4",
    "4:3",
    "4:5",
    "5:4",
    "9:16",
    "16:9",
    "21:9",
}


def _is_gemini_image_model(model: str) -> bool:
    if any(model.startswith(prefix) for prefix in GEMINI_IMAGE_MODEL_PREFIXES):
        return True
    return model.startswith("gemini-") and "image" in model


def _aspect_ratio_from_size(size: tuple[int, int] | None) -> str | None:
    if size is None:
        return None
    width, height = size
    if width <= 0 or height <= 0:
        return None
    factor = math.gcd(width, height)
    ratio = f"{width // factor}:{height // factor}"
    return ratio if ratio in _ALLOWED_ASPECT_RATIOS else None


def _image_size_from_size(size: tuple[int, int] | None) -> str | None:
    if size is None:
        return None
    largest = max(size)
    if largest <= 1024:
        return "1K"
    if largest <= 2048:
        return "2K"
    return "4K"


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
def _chat_impl(effect: LLMChat) -> EffectGenerator[str]:
    prompt = _messages_to_prompt(effect.messages)
    max_output_tokens = effect.max_tokens if effect.max_tokens is not None else 2048
    return (
        yield structured_llm__gemini(
            text=prompt,
            model=effect.model,
            temperature=effect.temperature,
            max_output_tokens=max_output_tokens,
            response_format=None,
        )
    )


@do
def _streaming_chat_impl(effect: LLMStreamingChat | LLMChat) -> EffectGenerator[str]:
    prompt = _messages_to_prompt(effect.messages)
    max_output_tokens = effect.max_tokens if effect.max_tokens is not None else 2048
    return (
        yield structured_llm__gemini(
            text=prompt,
            model=effect.model,
            temperature=effect.temperature,
            max_output_tokens=max_output_tokens,
            response_format=None,
        )
    )


@do
def _structured_impl(effect: LLMStructuredOutput) -> EffectGenerator[Any]:
    prompt = _messages_to_prompt(effect.messages)
    max_output_tokens = effect.max_tokens if effect.max_tokens is not None else 2048
    return (
        yield structured_llm__gemini(
            text=prompt,
            model=effect.model,
            temperature=effect.temperature,
            max_output_tokens=max_output_tokens,
            response_format=effect.response_format,
        )
    )


def _decode_image_bytes(payload: bytes) -> Image.Image:
    with BytesIO(payload) as buffer:
        image = Image.open(buffer)
        return image.copy()


def _prompt_for_generate(effect: ImageGenerate) -> str:
    prompt = effect.prompt
    if effect.style:
        prompt = f"{prompt}\nStyle: {effect.style}"
    if effect.negative_prompt:
        prompt = f"{prompt}\nNegative prompt: {effect.negative_prompt}"
    return prompt


def _gemini_to_unified(result: Any, *, model: str, prompt: str) -> ImageResult:
    return ImageResult(
        images=[_decode_image_bytes(result.image_bytes)],
        model=model,
        prompt=prompt,
        raw_response=result,
    )


@do
def _image_generate_impl(effect: ImageGenerate) -> EffectGenerator[ImageResult]:
    result = yield edit_image__gemini(
        prompt=_prompt_for_generate(effect),
        model=effect.model,
        images=None,
        candidate_count=max(1, effect.num_images),
        generation_config_overrides=effect.generation_config,
        aspect_ratio=_aspect_ratio_from_size(effect.size),
        image_size=_image_size_from_size(effect.size),
    )
    return _gemini_to_unified(result, model=effect.model, prompt=effect.prompt)


@do
def _image_edit_impl(effect: ImageEdit) -> EffectGenerator[ImageResult]:
    overrides = dict(effect.generation_config or {})
    if effect.strength != 0.8:
        overrides.setdefault("image_strength", effect.strength)
    result = yield edit_image__gemini(
        prompt=effect.prompt,
        model=effect.model,
        images=effect.images or None,
        generation_config_overrides=overrides or None,
    )
    return _gemini_to_unified(result, model=effect.model, prompt=effect.prompt)


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
def _embedding_impl(effect: LLMEmbedding) -> EffectGenerator[list[float] | list[list[float]]]:
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

    safe_result = yield Try(api_call_with_tracking())
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


def gemini_image_handler(effect: Any, k: Any):
    """Protocol handler with model routing for unified image effects."""
    if isinstance(effect, GeminiImageEdit):
        if not _is_gemini_image_model(effect.model):
            yield Delegate()
            return
        value = yield _image_edit_impl(effect)
        return (yield Resume(k, value))

    if isinstance(effect, ImageGenerate):
        if not _is_gemini_image_model(effect.model):
            yield Delegate()
            return
        value = yield _image_generate_impl(effect)
        return (yield Resume(k, value))

    if isinstance(effect, ImageEdit):
        if not _is_gemini_image_model(effect.model):
            yield Delegate()
            return
        value = yield _image_edit_impl(effect)
        return (yield Resume(k, value))

    yield Delegate()


def production_handlers(
    *,
    chat_impl: Callable[[LLMChat], EffectGenerator[str]] | None = None,
    streaming_chat_impl: Callable[[LLMStreamingChat | LLMChat], EffectGenerator[str]] | None = None,
    structured_impl: Callable[[LLMStructuredOutput], EffectGenerator[Any]] | None = None,
    embedding_impl: Callable[
        [LLMEmbedding],
        EffectGenerator[list[float] | list[list[float]]],
    ]
    | None = None,
    image_generate_impl: Callable[[ImageGenerate], EffectGenerator[ImageResult]] | None = None,
    image_edit_impl: Callable[[ImageEdit], EffectGenerator[ImageResult]] | None = None,
) -> ProtocolHandler:
    """Build a protocol handler backed by real Gemini API integrations."""

    active_chat_impl = chat_impl or _chat_impl
    active_streaming_chat_impl = streaming_chat_impl or _streaming_chat_impl
    active_structured_impl = structured_impl or _structured_impl
    active_embedding_impl = embedding_impl or _embedding_impl
    active_image_generate_impl = image_generate_impl or _image_generate_impl
    active_image_edit_impl = image_edit_impl or _image_edit_impl

    def handler(effect: Any, k: Any):
        if isinstance(effect, LLMStreamingChat | GeminiStreamingChat):
            if not _is_gemini_model(effect.model):
                yield Delegate()
                return
            value = yield active_streaming_chat_impl(effect)
            return (yield Resume(k, value))
        if isinstance(effect, LLMChat | GeminiChat):
            if not _is_gemini_model(effect.model):
                yield Delegate()
                return
            if effect.stream:
                value = yield active_streaming_chat_impl(effect)
                return (yield Resume(k, value))
            value = yield active_chat_impl(effect)
            return (yield Resume(k, value))
        if isinstance(effect, LLMStructuredOutput | GeminiStructuredOutput):
            if not _is_gemini_model(effect.model):
                yield Delegate()
                return
            value = yield active_structured_impl(effect)
            return (yield Resume(k, value))
        if isinstance(effect, LLMEmbedding | GeminiEmbedding):
            if not _is_gemini_model(effect.model):
                yield Delegate()
                return
            value = yield active_embedding_impl(effect)
            return (yield Resume(k, value))
        if isinstance(effect, ImageGenerate):
            if not _is_gemini_image_model(effect.model):
                yield Delegate()
                return
            value = yield active_image_generate_impl(effect)
            return (yield Resume(k, value))
        if isinstance(effect, ImageEdit | GeminiImageEdit):
            if not _is_gemini_image_model(effect.model):
                yield Delegate()
                return
            value = yield active_image_edit_impl(effect)
            return (yield Resume(k, value))
        yield Delegate()

    return handler


def gemini_production_handler(effect: Any, k: Any):
    """Single protocol handler suitable for ``WithHandler`` usage."""
    if isinstance(effect, LLMStreamingChat | GeminiStreamingChat):
        if _is_gemini_model(effect.model):
            value = yield _streaming_chat_impl(effect)
            return (yield Resume(k, value))
    elif isinstance(effect, LLMChat | GeminiChat):
        if _is_gemini_model(effect.model):
            if effect.stream:
                value = yield _streaming_chat_impl(effect)
                return (yield Resume(k, value))
            value = yield _chat_impl(effect)
            return (yield Resume(k, value))
    elif isinstance(effect, LLMStructuredOutput | GeminiStructuredOutput) and _is_gemini_model(
        effect.model
    ):
        value = yield _structured_impl(effect)
        return (yield Resume(k, value))
    elif isinstance(effect, LLMEmbedding | GeminiEmbedding) and _is_gemini_model(effect.model):
        value = yield _embedding_impl(effect)
        return (yield Resume(k, value))
    yield Delegate()


__all__ = [
    "GEMINI_IMAGE_MODEL_PREFIXES",
    "GEMINI_MODEL_EXACT",
    "GEMINI_MODEL_PREFIXES",
    "ProtocolHandler",
    "_is_gemini_model",
    "gemini_image_handler",
    "gemini_production_handler",
    "production_handlers",
]
