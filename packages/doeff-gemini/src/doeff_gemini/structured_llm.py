"""Structured LLM helper for Google Gemini built on top of doeff effects."""

from __future__ import annotations

import io
import json
import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from doeff import (
    Await,
    Catch,
    EffectGenerator,
    Fail,
    Log,
    Retry,
    Step,
    do,
)

from .client import get_gemini_client, track_api_call

if TYPE_CHECKING:  # pragma: no cover - optional dependency for type checkers
    import PIL.Image


def _stringify_for_log(content: Any, limit: int = 500) -> str:
    """Create a concise string representation for logging purposes."""
    if content is None:
        return ""
    try:
        if isinstance(content, str):
            text = content
        elif isinstance(content, (dict, list)):
            text = json.dumps(content)
        else:
            text = str(content)
    except Exception:  # pragma: no cover - defensive
        text = str(content)
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def _image_to_part(image: PIL.Image.Image):
    """Convert a PIL image into a Gemini content part."""
    from google.genai import types

    buffer = io.BytesIO()
    image_format = (image.format or "PNG").upper()
    image.save(buffer, format=image_format)
    mime_type = f"image/{image_format.lower()}"
    return types.Part.from_bytes(data=buffer.getvalue(), mime_type=mime_type)


def _extract_text_from_response(response: Any) -> str:
    """Collect textual fragments from a Gemini response object."""
    if response is None:
        return ""
    text_attr = getattr(response, "text", None)
    if text_attr:
        return text_attr
    fragments: list[str] = []
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if content is None:
            continue
        parts = getattr(content, "parts", None)
        if parts is None:
            part_text = getattr(content, "text", None)
            if part_text:
                fragments.append(part_text)
            continue
        for part in parts:
            if isinstance(part, dict):
                part_text = part.get("text")
                if part_text:
                    fragments.append(str(part_text))
                continue
            part_text = getattr(part, "text", None)
            if part_text:
                fragments.append(part_text)
    return "\n".join(fragment for fragment in fragments if fragment).strip()


@do
def build_contents(
    text: str,
    images: list[PIL.Image.Image] | None = None,
) -> EffectGenerator[list[Any]]:
    """Prepare the list of :mod:`google.genai` contents to feed into Gemini."""
    from google.genai import types

    image_count = len(images) if images else 0
    yield Log(f"Building Gemini prompt with {image_count} image(s)")
    parts: list[Any] = []
    if images:
        for idx, image in enumerate(images):
            yield Log(f"Embedding image {idx + 1}/{image_count}")
            parts.append(_image_to_part(image))
    parts.append(types.Part.from_text(text=text))
    contents = [types.Content(role="user", parts=parts)]
    return contents


@do
def build_generation_config(
    *,
    temperature: float,
    max_output_tokens: int,
    top_p: float | None,
    top_k: int | None,
    candidate_count: int,
    system_instruction: str | None,
    safety_settings: list[dict[str, Any]] | None,
    tools: list[dict[str, Any]] | None,
    tool_config: dict[str, Any] | None,
    response_format: type[BaseModel] | None,
    generation_config_overrides: dict[str, Any] | None,
) -> EffectGenerator[Any]:
    """Create the :class:`google.genai.types.GenerateContentConfig` payload."""
    from google.genai import types

    config_data: dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "candidate_count": candidate_count,
    }
    if top_p is not None:
        config_data["top_p"] = top_p
    if top_k is not None:
        config_data["top_k"] = top_k
    if system_instruction:
        config_data["system_instruction"] = system_instruction
    if safety_settings:
        config_data["safety_settings"] = safety_settings
    if tools:
        config_data["tools"] = tools
    if tool_config:
        config_data["tool_config"] = tool_config
    if response_format is not None and issubclass(response_format, BaseModel):
        config_data["response_schema"] = response_format
        config_data.setdefault("response_mime_type", "application/json")
    if generation_config_overrides:
        config_data.update({k: v for k, v in generation_config_overrides.items() if v is not None})

    try:
        config = types.GenerateContentConfig(**config_data)
    except ValidationError as exc:
        yield Log(f"Invalid Gemini generation configuration: {exc}")
        yield Fail(exc)

    yield Log(
        "Generation config prepared: "
        + ", ".join(f"{key}={value}" for key, value in config_data.items() if value is not None)
    )
    return config


@do
def process_structured_response(
    response: Any,
    response_format: type[BaseModel],
) -> EffectGenerator[Any]:
    """Parse a structured Gemini response into the provided Pydantic model."""
    parsed_candidate = getattr(response, "parsed", None)
    payload: Any | None = None

    if parsed_candidate:
        candidate = parsed_candidate[0] if isinstance(parsed_candidate, list) else parsed_candidate
        if isinstance(candidate, response_format):
            yield Log("Gemini provided pre-parsed structured output")
            return candidate
        if isinstance(candidate, BaseModel):
            payload = candidate.model_dump()
        elif isinstance(candidate, dict):
            payload = candidate
        else:
            payload = candidate
        preview = _stringify_for_log(payload, limit=200)
        yield Log(f"Parsing structured payload from parsed field: {preview}")
    else:
        raw_text = _extract_text_from_response(response)
        preview = _stringify_for_log(raw_text, limit=200)
        yield Log(f"Parsing Gemini structured response: {preview}")
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            yield Log(f"Failed to decode JSON from Gemini response: {exc}")
            yield Log(f"Raw content: {preview}")
            yield Fail(exc)

    try:
        if hasattr(response_format, "model_validate"):
            result = response_format.model_validate(payload)  # type: ignore[attr-defined]
        else:
            result = response_format(**payload)
    except ValidationError as exc:
        preview = _stringify_for_log(payload, limit=200)
        yield Log(f"Structured response validation error: {exc}")
        yield Log(f"Raw content: {preview}")
        yield Fail(exc)
    return result


@do
def process_unstructured_response(response: Any) -> EffectGenerator[str]:
    """Return the best-effort textual output from the Gemini response."""
    text = _extract_text_from_response(response)
    preview = _stringify_for_log(text, limit=200)
    yield Log(f"Received Gemini response: {preview}")
    return text


@do
def structured_llm__gemini(
    text: str,
    model: str = "gemini-1.5-pro-latest",
    images: list[PIL.Image.Image] | None = None,
    response_format: type[BaseModel] | None = None,
    max_output_tokens: int = 2048,
    temperature: float = 0.7,
    top_p: float | None = None,
    top_k: int | None = None,
    candidate_count: int = 1,
    system_instruction: str | None = None,
    safety_settings: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_config: dict[str, Any] | None = None,
    generation_config_overrides: dict[str, Any] | None = None,
    max_retries: int = 3,
) -> EffectGenerator[Any]:
    """High level helper mirroring ``structured_llm__openai`` for Gemini models."""
    yield Log(f"Preparing Gemini structured call using model={model}")

    client = yield get_gemini_client()
    async_client = client.async_client

    contents = yield build_contents(text=text, images=images)

    generation_config = yield build_generation_config(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        top_p=top_p,
        top_k=top_k,
        candidate_count=candidate_count,
        system_instruction=system_instruction,
        safety_settings=safety_settings,
        tools=tools,
        tool_config=tool_config,
        response_format=response_format,
        generation_config_overrides=generation_config_overrides,
    )

    generation_config_payload = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "top_p": top_p,
        "top_k": top_k,
        "candidate_count": candidate_count,
        "system_instruction": system_instruction,
        "safety_settings": safety_settings,
        "tools": tools,
        "tool_config": tool_config,
        "response_format": response_format.__name__ if response_format else None,
        "generation_config_overrides": generation_config_overrides,
    }

    request_payload = {
        "text": text,
        "images": images or [],
        "generation_config": {
            key: value
            for key, value in generation_config_payload.items()
            if value is not None
        },
    }

    request_summary = {
        "operation": "generate_content",
        "model": model,
        "has_images": bool(images),
        "candidate_count": candidate_count,
        "response_schema": response_format.__name__ if response_format else None,
    }
    request_summary = {k: v for k, v in request_summary.items() if v is not None}

    @do
    def make_api_call() -> EffectGenerator[Any]:
        attempt_start_time = time.time()

        @do
        def api_call_with_tracking() -> EffectGenerator[Any]:
            response = yield Await(
                async_client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=generation_config,
                )
            )
            yield track_api_call(
                operation="generate_content",
                model=model,
                request_summary=request_summary,
                request_payload=request_payload,
                response=response,
                start_time=attempt_start_time,
                error=None,
            )
            return response

        @do
        def handle_error(exc: Exception) -> EffectGenerator[None]:
            yield track_api_call(
                operation="generate_content",
                model=model,
                request_summary=request_summary,
                request_payload=request_payload,
                response=None,
                start_time=attempt_start_time,
                error=exc,
            )
            yield Fail(exc)

        response = yield Catch(api_call_with_tracking(), handle_error)
        return response

    response = yield Retry(make_api_call(), max_attempts=max_retries, delay_ms=1000)

    if response_format is not None and issubclass(response_format, BaseModel):
        result = yield process_structured_response(response, response_format)
    else:
        result = yield process_unstructured_response(response)

    yield Step(
        value={"result_type": type(result).__name__ if response_format else "str"},
        meta={"model": model},
    )

    return result


__all__ = [
    "build_contents",
    "build_generation_config",
    "process_structured_response",
    "process_unstructured_response",
    "structured_llm__gemini",
]
