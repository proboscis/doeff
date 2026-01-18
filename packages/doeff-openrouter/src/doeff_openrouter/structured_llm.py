"""Structured output helpers for OpenRouter models."""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from doeff import (
    EffectGenerator,
    Fail,
    Log,
    do,
)

from .chat import chat_completion

if TYPE_CHECKING:  # pragma: no cover - optional dependency for type checking
    import PIL.Image


def convert_pil_to_base64(image: PIL.Image.Image) -> str:
    """Encode a PIL image as a data URL accepted by OpenRouter."""
    buffer = io.BytesIO()
    image_format = (image.format or "PNG").upper()
    image.save(buffer, format=image_format)
    img_bytes = buffer.getvalue()
    encoded = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:image/{image_format.lower()};base64,{encoded}"


def _collect_message_content_parts(content: Any) -> tuple[Any | None, list[str]]:
    """Extract JSON payload (if any) and text fragments from message content."""
    if content is None:
        return None, []
    if isinstance(content, str):
        return None, [content]

    json_payload: Any | None = None
    text_parts: list[str] = []

    if isinstance(content, list):
        for part in content:
            part_json = None
            if isinstance(part, dict):
                part_json = part.get("json")
                part_text = (
                    part.get("text")
                    or part.get("input_text")
                    or part.get("output_text")
                    or part.get("content")
                )
                if part_text is not None:
                    if isinstance(part_text, list):
                        text_parts.extend(str(item) for item in part_text if item is not None)
                    else:
                        text_parts.append(str(part_text))
            else:
                part_json = getattr(part, "json", None)
                part_text = getattr(part, "text", None) or getattr(part, "content", None)
                if part_text is not None:
                    text_parts.append(str(part_text))

            if json_payload is None and part_json is not None:
                json_payload = part_json
            if json_payload is None and isinstance(part_text, (dict, list)):
                json_payload = part_text
    else:
        text_parts.append(str(content))

    return json_payload, text_parts


def _stringify_for_log(content: Any, limit: int = 400) -> str:
    """Produce a condensed preview used in logs."""
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


@do
def build_messages(
    text: str,
    *,
    system_prompt: str | None = None,
    images: list[PIL.Image.Image] | None = None,
    extra_messages: list[dict[str, Any]] | None = None,
) -> EffectGenerator[list[dict[str, Any]]]:
    """Construct OpenRouter chat messages for the request."""
    messages: list[dict[str, Any]] = []
    if system_prompt:
        yield Tell("Adding system prompt to OpenRouter request")
        messages.append({"role": "system", "content": system_prompt})

    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    if images:
        yield Tell(f"Embedding {len(images)} image(s) into request")
        for idx, image in enumerate(images):
            try:
                data_url = convert_pil_to_base64(image)
            except Exception as exc:  # pragma: no cover - defensive
                yield Tell(f"Failed to encode image {idx + 1}: {exc}")
                raise exc
            parts.append({"type": "image_url", "image_url": {"url": data_url}})

    messages.append({"role": "user", "content": parts})

    if extra_messages:
        yield Tell(f"Appending {len(extra_messages)} extra message(s) to conversation")
        messages.extend(extra_messages)

    return messages


def ensure_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Force additionalProperties to False recursively for object schemas."""
    if not isinstance(schema, dict):
        return schema
    schema_type = schema.get("type")
    if schema_type == "object":
        schema.setdefault("additionalProperties", False)
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for value in properties.values():
                ensure_strict_schema(value)
    if schema_type == "array" and "items" in schema:
        ensure_strict_schema(schema["items"])
    for key, value in list(schema.items()):
        if isinstance(value, dict):
            ensure_strict_schema(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    ensure_strict_schema(item)
    return schema


def build_response_format_payload(response_format: type[BaseModel]) -> dict[str, Any]:
    """Convert a Pydantic model into an OpenRouter JSON schema payload."""
    schema = response_format.model_json_schema()
    ensure_strict_schema(schema)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": response_format.__name__,
            "schema": schema,
            "strict": True,
        },
    }


def _extract_choice(response: dict[str, Any]) -> dict[str, Any] | None:
    choices = response.get("choices") if isinstance(response, dict) else None
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            return first
    return None


def process_unstructured_response(response: dict[str, Any]) -> str:
    """Flatten assistant content into a plain text string."""
    choice = _extract_choice(response)
    if not choice:
        return ""
    message = choice.get("message", {}) if isinstance(choice, dict) else {}
    content = message.get("content")
    _, text_parts = _collect_message_content_parts(content)
    if not text_parts:
        text = message.get("text")
        if isinstance(text, str):
            text_parts = [text]
    return "\n".join(part.strip() for part in text_parts if part).strip()


def _strip_code_fence(text: str) -> str:
    if "```" not in text:
        return text
    segments = text.split("```")
    if len(segments) < 2:
        return text
    candidate = segments[1].strip()
    if candidate.startswith("json\n"):
        candidate = candidate[5:]
    elif candidate.startswith("json\r\n"):
        candidate = candidate[6:]
    return candidate.strip()


def _first_choice(response: dict[str, Any]) -> Mapping[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("OpenRouter response did not contain a choices array with entries")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise RuntimeError("OpenRouter response choice payload must be an object")
    return first


def _message_from_choice(choice: Mapping[str, Any]) -> Mapping[str, Any]:
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise RuntimeError("OpenRouter response choice missing a message object")
    return message


def _validate_with_model(response_format: type[BaseModel], payload: Any) -> BaseModel:
    if hasattr(response_format, "model_validate"):
        return response_format.model_validate(payload)  # type: ignore[attr-defined]
    if hasattr(response_format, "parse_obj"):
        return response_format.parse_obj(payload)  # type: ignore[attr-defined]
    return response_format(**payload)


@do
def process_structured_response(
    response: dict[str, Any],
    response_format: type[BaseModel],
) -> EffectGenerator[BaseModel]:
    """Parse a structured response into the requested Pydantic model."""
    try:
        choice = _first_choice(response)
    except RuntimeError as exc:
        yield Tell("OpenRouter response did not contain choices")
        raise exc

    try:
        message = _message_from_choice(choice)
    except RuntimeError as exc:
        yield Tell(str(exc))
        raise

    candidate_payload: Any
    preview_source = ""
    parsed = message.get("parsed")
    if parsed not in (None, {}):
        yield Tell("Ignoring unexpected OpenRouter parsed payload; using content field instead")
    content = message.get("content")
    if not isinstance(content, str):
        raise RuntimeError("OpenRouter structured responses must provide string message content")

    cleaned = _strip_code_fence(content).strip()
    preview_source = _stringify_for_log(cleaned)
    if not cleaned:
        raise RuntimeError("OpenRouter structured responses returned empty content")

    try:
        candidate_payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        yield Tell(f"JSON decoding failed: {exc}. Payload preview: {preview_source}")
        raise StructuredOutputParsingError("OpenRouter response did not contain valid JSON content") from exc

    try:
        result = _validate_with_model(response_format, candidate_payload)
    except ValidationError as exc:
        yield Tell(f"Structured response validation failed: {exc}")
        yield Tell(f"Payload preview: {preview_source}")
        raise exc

    return result


@do
def structured_llm(
    text: str,
    *,
    model: str,
    response_format: type[BaseModel] | None = None,
    images: list[PIL.Image.Image] | None = None,
    system_prompt: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    provider: dict[str, Any] | None = None,
    include_reasoning: bool = False,
    reasoning: dict[str, Any] | None = None,
    extra_messages: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> EffectGenerator[Any]:
    """High-level helper that produces structured results with OpenRouter."""
    yield Tell(f"Preparing structured request for model={model}")
    messages = yield build_messages(
        text,
        system_prompt=system_prompt,
        images=images,
        extra_messages=extra_messages,
    )

    response_format_payload = None
    expects_structure = (
        response_format is not None and isinstance(response_format, type) and issubclass(response_format, BaseModel)
    )
    if expects_structure:
        response_format_payload = build_response_format_payload(response_format)
        yield Tell("Attached JSON schema payload for structured output")

    response = yield chat_completion(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        provider=provider,
        include_reasoning=include_reasoning,
        reasoning=reasoning,
        response_format=response_format_payload,
        **kwargs,
    )

    if expects_structure and response_format is not None:
        return (yield process_structured_response(response, response_format))
    return process_unstructured_response(response)


__all__ = [
    "build_messages",
    "build_response_format_payload",
    "ensure_strict_schema",
    "process_structured_response",
    "process_unstructured_response",
    "structured_llm",
]
class StructuredOutputParsingError(RuntimeError):
    """Raised when the provider returns content that cannot be parsed as JSON."""
