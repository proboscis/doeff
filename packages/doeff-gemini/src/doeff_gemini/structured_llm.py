"""Structured LLM helper for Google Gemini built on top of doeff effects."""

from __future__ import annotations

import base64
import io
import json
import random
import textwrap
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel, ValidationError

from doeff import (
    Ask,
    Await,
    Catch,
    EffectGenerator,
    Fail,
    Log,
    Recover,
    Retry,
    Step,
    do,
    slog,
)

from .client import get_gemini_client, track_api_call
from .types import GeminiImageEditResult

if TYPE_CHECKING:  # pragma: no cover - optional dependency for type checkers
    import PIL.Image


class GeminiStructuredOutputError(ValueError):
    """Raised when Gemini returns content that cannot be parsed as the requested schema."""

    def __init__(self, *, format_name: str, raw_content: str, message: str) -> None:
        super().__init__(message)
        self.format_name = format_name
        self.raw_content = raw_content


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


_DEFAULT_JSON_FIX_MODEL = "gemini-2.5-pro"
_DEFAULT_JSON_FIX_MAX_OUTPUT_TOKENS = 2048
_RANDOM_BACKOFF_MIN_DELAY_SECONDS = 1.0
_RANDOM_BACKOFF_MAX_DELAY_SECONDS = 30.0


def _gemini_random_backoff(attempt: int, error: Exception | None) -> float:
    """Compute a jittered delay before the next Gemini retry attempt."""
    _ = error  # error currently unused but kept for future heuristics
    upper = _RANDOM_BACKOFF_MIN_DELAY_SECONDS * (2 ** (attempt - 1))
    upper = min(
        _RANDOM_BACKOFF_MAX_DELAY_SECONDS,
        max(_RANDOM_BACKOFF_MIN_DELAY_SECONDS, upper),
    )
    return random.uniform(_RANDOM_BACKOFF_MIN_DELAY_SECONDS, upper)


def _make_gemini_json_fix_sllm(
    *,
    model: str,
    max_output_tokens: int,
    system_instruction: str | None,
    safety_settings: list[dict[str, Any]] | None,
    tools: list[dict[str, Any]] | None,
    tool_config: dict[str, Any] | None,
    generation_config_overrides: dict[str, Any] | None,
) -> Callable[[str, type[BaseModel]], EffectGenerator[Any]]:
    """Create an ``sllm_for_json_fix`` implementation bound to specific config."""

    @do
    def _impl(
        json_text: str, response_format: type[BaseModel]
    ) -> EffectGenerator[Any]:
        return (
            yield _gemini_json_fix(
                model=model,
                response_format=response_format,
                malformed_content=json_text,
                max_output_tokens=max_output_tokens,
                system_instruction=system_instruction,
                safety_settings=safety_settings,
                tools=tools,
                tool_config=tool_config,
                generation_config_overrides=generation_config_overrides,
            )
        )

    return _impl


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


def _extract_json_payload_from_response(response: Any) -> Any | None:
    """Pull the first JSON-compatible payload exposed by the Gemini SDK."""

    def _to_sequence(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value]

    def _iter_parts(container: Any) -> list[Any]:
        if container is None:
            return []
        if isinstance(container, dict):
            parts = container.get("parts")
        else:
            parts = getattr(container, "parts", None)
        return _to_sequence(parts)

    candidate_sources: list[Any] = []
    for attr in ("candidates", "output", "outputs"):
        value = getattr(response, attr, None)
        if isinstance(value, dict):
            value = value.get(attr)
        candidate_sources.extend(_to_sequence(value))

    for candidate in candidate_sources:
        contents: list[Any] = []
        if isinstance(candidate, dict):
            contents.extend(_to_sequence(candidate.get("content")))
            contents.extend(_to_sequence(candidate.get("contents")))
        else:
            contents.extend(_to_sequence(getattr(candidate, "content", None)))
            contents.extend(_to_sequence(getattr(candidate, "contents", None)))

        for content in contents:
            for part in _iter_parts(content):
                if isinstance(part, dict):
                    json_payload = part.get("json") or part.get("data")
                else:
                    json_payload = getattr(part, "json", None) or getattr(part, "data", None)

                if isinstance(json_payload, BaseModel):
                    return json_payload.model_dump()
                if isinstance(json_payload, (dict, list, str)) and json_payload:
                    return json_payload
    return None


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
    response_modalities: list[str] | None = None,
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
    if response_modalities:
        config_data["response_modalities"] = response_modalities
    if generation_config_overrides:
        config_data.update({k: v for k, v in generation_config_overrides.items() if v is not None})

    try:
        config = types.GenerateContentConfig(**config_data)
    except ValidationError as exc:
        yield Log(f"Invalid Gemini generation configuration: {exc}")
        raise

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
    format_name = f"{response_format.__module__}.{response_format.__qualname__}"

    if parsed_candidate: # why dont we just return the parsed???
        if isinstance(parsed_candidate, (list, tuple)):
            non_null = [item for item in parsed_candidate if item is not None]
            candidate = non_null[0] if non_null else None
        else:
            candidate = parsed_candidate

        if candidate is not None:
            if isinstance(candidate, response_format):
                yield Log("Gemini provided pre-parsed structured output")
                return candidate
            if isinstance(candidate, BaseModel):
                payload = candidate.model_dump()
            elif isinstance(candidate, dict):
                payload = candidate
            elif isinstance(candidate, list):  # pragma: no cover - defensive
                payload = candidate[0] if candidate else None
            else:
                payload = candidate

        preview = _stringify_for_log(payload, limit=200)
        yield Log(f"Parsing structured payload from parsed field: {preview}")
    else:
        payload = _extract_json_payload_from_response(response)

        if payload is not None and isinstance(payload, str):
            stripped = payload.strip()
            if stripped:
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    preview = _stringify_for_log(stripped, limit=200)
                    raw_content_for_error = stripped
                    yield Log("Gemini json payload could not be decoded")
                    raise GeminiStructuredOutputError(
                        format_name=format_name,
                        raw_content=stripped,
                        message=(
                            f"Gemini returned invalid structured payload for {format_name}: {preview}"
                        ),
                    )
            else:
                payload = None

        if payload is None:
            raw_text = _extract_text_from_response(response)
            preview = _stringify_for_log(raw_text, limit=200)
            yield Log(f"Parsing Gemini structured response fall back to text: {preview}")
            stripped = raw_text.strip()
            raw_content_for_error = raw_text
            if stripped and stripped[0] in "[{":
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    yield Log("Gemini response text was not valid JSON")
                    yield Log(f"Raw content: {preview}")
                    raise GeminiStructuredOutputError(
                        format_name=format_name,
                        raw_content=raw_text,
                        message=(
                            f"Gemini returned non-JSON structured output for {format_name}: {preview}"
                        ),
                    )
            else:
                yield Log("Gemini response did not include JSON payload")
                raise GeminiStructuredOutputError(
                    format_name=format_name,
                    raw_content=raw_text,
                    message=(
                        f"Gemini missing structured JSON payload for {format_name}: {preview}"
                    ),
                )

    try:
        if hasattr(response_format, "model_validate"):
            result = response_format.model_validate(payload)  # type: ignore[attr-defined]
        else:
            result = response_format(**payload)
    except ValidationError as exc:
        preview = _stringify_for_log(payload, limit=200)
        yield Log(f"Structured response validation error: {exc}")
        yield Log(f"Raw content: {preview}")
        raise
    return result


@do
def process_unstructured_response(response: Any) -> EffectGenerator[str]:
    """Return the best-effort textual output from the Gemini response."""
    text = _extract_text_from_response(response)
    preview = _stringify_for_log(text, limit=200)
    yield Log(f"Received Gemini response: {preview}")
    return text


@do
def _gemini_json_fix(
    *,
    model: str,
    response_format: type[BaseModel],
    malformed_content: str,
    max_output_tokens: int,
    system_instruction: str | None,
    safety_settings: list[dict[str, Any]] | None,
    tools: list[dict[str, Any]] | None,
    tool_config: dict[str, Any] | None,
    generation_config_overrides: dict[str, Any] | None,
) -> EffectGenerator[Any]:
    """Default Gemini-backed JSON repair routine."""

    yield Log("Attempting Gemini structured output repair with second call")

    client = yield get_gemini_client()
    async_client = client.async_client

    schema_json = json.dumps(
        response_format.model_json_schema(mode="validation"), indent=2, sort_keys=True
    )

    repair_instruction = (
        (system_instruction + "\n\n") if system_instruction else ""
    ) + "You must return only valid JSON that strictly matches the provided schema."

    prompt = textwrap.dedent(
        f"""
        You previously produced output that failed to parse as the required JSON schema.
        Rewrite the response as valid JSON that matches the schema.
        Do not add any explanation, markdown, or commentary.

        JSON schema:
        ```json
        {schema_json}
        ```
        Malformed response:
        ```
        {malformed_content}
        ```

        Return only valid JSON.
        """
    ).strip()

    contents = yield build_contents(text=prompt, images=None)

    generation_config = yield build_generation_config(
        temperature=0.0,
        max_output_tokens=max_output_tokens,
        top_p=None,
        top_k=None,
        candidate_count=1,
        system_instruction=repair_instruction,
        safety_settings=safety_settings,
        tools=tools,
        tool_config=tool_config,
        response_format=response_format,
        response_modalities=None,
        generation_config_overrides=generation_config_overrides,
    )

    attempt_start_time = time.time()

    generation_config_payload = {
        "temperature": 0.0,
        "max_output_tokens": max_output_tokens,
        "top_p": None,
        "top_k": None,
        "candidate_count": 1,
        "system_instruction": repair_instruction,
        "safety_settings": safety_settings,
        "tools": tools,
        "tool_config": tool_config,
        "response_format": response_format.__name__,
        "generation_config_overrides": generation_config_overrides,
    }

    api_payload = {
        "model": model,
        "contents": contents,
        "config": generation_config,
    }

    request_payload = {
        "text": prompt,
        "images": [],
        "generation_config": {
            key: value
            for key, value in generation_config_payload.items()
            if value is not None
        },
    }

    request_summary = {
        "operation": "repair_structured_output",
        "model": model,
        "response_schema": response_format.__name__,
    }

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
            operation="repair_structured_output",
            model=model,
            request_summary=request_summary,
            request_payload=request_payload,
            response=response,
            start_time=attempt_start_time,
            error=None,
            api_payload=api_payload,
        )
        return response

    @do
    def handle_error(exc: Exception) -> EffectGenerator[None]:
        yield track_api_call(
            operation="repair_structured_output",
            model=model,
            request_summary=request_summary,
            request_payload=request_payload,
            response=None,
            start_time=attempt_start_time,
            error=exc,
            api_payload=api_payload,
        )
        raise exc

    response = yield Catch(api_call_with_tracking(), handle_error)

    repaired = yield process_structured_response(response, response_format)
    return repaired


@do
def gemini_sllm_for_json_fix(
    json_text: str, response_format: type[BaseModel]
) -> EffectGenerator[Any]:
    """Default Gemini-based JSON repair using hard-coded configuration."""

    return (
        yield _gemini_json_fix(
            model=_DEFAULT_JSON_FIX_MODEL,
            response_format=response_format,
            malformed_content=json_text,
            max_output_tokens=_DEFAULT_JSON_FIX_MAX_OUTPUT_TOKENS,
            system_instruction=None,
            safety_settings=None,
            tools=None,
            tool_config=None,
            generation_config_overrides=None,
        )
    )


@do
def repair_structured_response(
    *,
    model: str,
    response_format: type[BaseModel],
    malformed_content: str,
    max_output_tokens: int,
    default_sllm: Callable[[str, type[BaseModel]], EffectGenerator[Any]] | None = None,
) -> EffectGenerator[Any]:
    """Repair malformed JSON by delegating to an injectable structured LLM."""

    fallback_sllm = default_sllm or gemini_sllm_for_json_fix

    sllm_for_json_fix = yield Recover(Ask("sllm_for_json_fix"), lambda _: fallback_sllm)

    if sllm_for_json_fix is fallback_sllm:
        yield Log("sllm_for_json_fix not provided. Falling back to default Gemini repair")
    else:
        yield Log("Using environment-provided sllm_for_json_fix for structured repair")

    fixed_value = yield sllm_for_json_fix(malformed_content, response_format=response_format)

    if fixed_value is None:
        raise ValueError("sllm_for_json_fix returned None, cannot repair structured response")

    if isinstance(fixed_value, response_format):
        return fixed_value

    if isinstance(fixed_value, BaseModel):
        payload: Mapping[str, Any] | None = fixed_value.model_dump()
    elif isinstance(fixed_value, Mapping):
        payload = dict(fixed_value)
    else:
        if not isinstance(fixed_value, str):
            fixed_text = str(fixed_value)
        else:
            fixed_text = fixed_value
        try:
            payload = json.loads(fixed_text)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive logging
            preview = _stringify_for_log(fixed_text, limit=200)
            yield Log(f"sllm_for_json_fix returned non-JSON payload: {preview}")
            raise ValueError("sllm_for_json_fix returned invalid JSON") from exc

    if payload is None:
        raise ValueError("sllm_for_json_fix returned empty payload")

    try:
        if hasattr(response_format, "model_validate"):
            result = response_format.model_validate(payload)  # type: ignore[attr-defined]
        else:
            result = response_format(**payload)
    except ValidationError as exc:
        preview = _stringify_for_log(payload, limit=200)
        yield Log(f"Structured response validation error after repair: {exc}")
        yield Log(f"Raw content: {preview}")
        raise

    return result


@do
def process_image_edit_response(response: Any) -> EffectGenerator[GeminiImageEditResult]:
    """Extract image bytes and optional text from a Gemini response."""

    image_bytes: bytes | None = None
    mime_type: str | None = None
    text_fragments: list[str] = []

    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if content is None:
            continue
        parts = getattr(content, "parts", None) or []
        if not parts:
            part_text = getattr(content, "text", None)
            if part_text:
                text_fragments.append(part_text)
            continue
        for part in parts:
            part_text: str | None = None
            inline_data: Any | None = None
            if isinstance(part, dict):
                part_text = part.get("text")
                inline_data = part.get("inline_data") or part.get("inlineData")
            else:
                part_text = getattr(part, "text", None)
                inline_data = getattr(part, "inline_data", None) or getattr(part, "inlineData", None)

            if part_text:
                text_fragments.append(str(part_text))
                continue

            if inline_data is None or image_bytes is not None:
                continue

            if isinstance(inline_data, dict):
                data = inline_data.get("data")
                mime = inline_data.get("mime_type") or inline_data.get("mimeType")
            else:
                data = getattr(inline_data, "data", None)
                mime = getattr(inline_data, "mime_type", None) or getattr(inline_data, "mimeType", None)

            if isinstance(data, str):
                try:
                    data = base64.b64decode(data)
                except Exception:  # pragma: no cover - fall back if decoding fails
                    data = data.encode("utf-8")

            if data is None:
                continue

            image_bytes = bytes(data)
            mime_type = mime or "image/png"

    if image_bytes is None or mime_type is None:
        yield Log("Gemini response did not include edited image data")
        yield Fail(ValueError("Gemini response missing edited image"))

    combined_text = "\n".join(text_fragments) if text_fragments else None
    text_preview = _stringify_for_log(combined_text, limit=200)
    yield Log(f"Gemini image edit text preview: {text_preview}")

    return GeminiImageEditResult(
        image_bytes=image_bytes,
        mime_type=mime_type,
        text=combined_text,
    )


@do
def structured_llm__gemini(
    text: str,
    model: str = "gemini-2.5-pro",
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
        response_modalities=None,
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

    api_payload = {
        "model": model,
        "contents": contents,
        "config": generation_config,
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
                api_payload=api_payload,
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
                api_payload=api_payload,
            )
            raise exc

        return (yield Catch(api_call_with_tracking(), handle_error))

    @do
    def handle_retry_exhaustion(exc: Exception) -> EffectGenerator[Any]:
        yield slog(
            event="gemini.retry_exhausted",
            level="ERROR",
            model=model,
            operation="generate_content",
            attempts=max_retries,
            error=str(exc),
        )
        raise exc

    response = yield Catch(
        Retry(
            make_api_call(),
            max_attempts=max_retries,
            delay_strategy=_gemini_random_backoff,
        ),
        handle_retry_exhaustion,
    )

    if response_format is not None and issubclass(response_format, BaseModel):
        @do
        def handle_structured_error(exc: Exception) -> EffectGenerator[Any]:
            if isinstance(exc, GeminiStructuredOutputError):
                default_sllm = _make_gemini_json_fix_sllm(
                    model=model,
                    max_output_tokens=max_output_tokens,
                    system_instruction=system_instruction,
                    safety_settings=safety_settings,
                    tools=tools,
                    tool_config=tool_config,
                    generation_config_overrides=generation_config_overrides,
                )
                return (
                    yield repair_structured_response(
                        model=model,
                        response_format=response_format,
                        malformed_content=exc.raw_content,
                        max_output_tokens=max_output_tokens,
                        default_sllm=default_sllm,
                    )
                )
            raise exc

        result = yield Catch(
            process_structured_response(response, response_format),
            handle_structured_error,
        )
    else:
        result = yield process_unstructured_response(response)

    yield Step(
        value={"result_type": type(result).__name__ if response_format else "str"},
        meta={"model": model},
    )

    return result


@do
def edit_image__gemini(
    prompt: str,
    model: str = "gemini-2.5-flash-image-preview",
    images: list[PIL.Image.Image] | None = None,
    max_output_tokens: int = 8192,
    temperature: float = 0.9,
    top_p: float | None = None,
    top_k: int | None = None,
    candidate_count: int = 1,
    system_instruction: str | None = None,
    safety_settings: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_config: dict[str, Any] | None = None,
    response_modalities: list[str] | None = None,
    generation_config_overrides: dict[str, Any] | None = None,
    max_retries: int = 3,
) -> EffectGenerator[GeminiImageEditResult]:
    """Generate or edit an image using Gemini multimodal models."""

    yield Log(
        "Preparing Gemini image edit call using model="
        f"{model} with {len(images) if images else 0} input image(s)"
    )

    client = yield get_gemini_client()
    async_client = client.async_client

    contents = yield build_contents(text=prompt, images=images)

    response_modalities = list(response_modalities or ["TEXT", "IMAGE"])

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
        response_format=None,
        response_modalities=response_modalities,
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
        "response_modalities": response_modalities,
        "generation_config_overrides": generation_config_overrides,
    }

    api_payload = {
        "model": model,
        "contents": contents,
        "config": generation_config,
    }

    request_payload = {
        "text": prompt,
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
        "response_modalities": response_modalities,
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
                api_payload=api_payload,
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
                api_payload=api_payload,
            )
            yield Fail(exc)

        return (yield Catch(api_call_with_tracking(), handle_error))

    @do
    def handle_retry_exhaustion(exc: Exception) -> EffectGenerator[Any]:
        yield slog(
            event="gemini.retry_exhausted",
            level="ERROR",
            model=model,
            operation="generate_content",
            attempts=max_retries,
            error=str(exc),
        )
        raise exc

    response = yield Catch(
        Retry(
            make_api_call(),
            max_attempts=max_retries,
            delay_strategy=_gemini_random_backoff,
        ),
        handle_retry_exhaustion,
    )

    result = yield process_image_edit_response(response)

    yield Step(
        value={
            "result_type": type(result).__name__,
            "has_text": bool(result.text),
        },
        meta={
            "model": model,
            "input_image_count": len(images) if images else 0,
        },
    )

    return result


__all__ = [
    "build_contents",
    "build_generation_config",
    "process_structured_response",
    "process_unstructured_response",
    "process_image_edit_response",
    "structured_llm__gemini",
    "edit_image__gemini",
]
