# ruff: noqa: E402
"""Tests for the Gemini structured LLM implementation."""

import importlib
import json
import math
import os
import sys
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

IMAGE_PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "doeff-image" / "src"
if str(IMAGE_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(IMAGE_PACKAGE_ROOT))

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))


from PIL import Image

google_genai = pytest.importorskip("google.genai")
from doeff_gemini import (
    GeminiImageEditResult,
    build_contents,
    build_generation_config,
    edit_image__gemini,
    image_edit__gemini,
    process_image_edit_response,
    process_structured_response,
    process_unstructured_response,
    structured_llm__gemini,
)
from doeff_gemini.client import track_api_call
from doeff_gemini.costs import calculate_cost
from doeff_gemini.handlers import default_gemini_cost_handler
from doeff_gemini.structured_llm import GeminiStructuredOutputError
from doeff_gemini.types import APICallMetadata

genai_types = google_genai.types
from pydantic import BaseModel

from doeff import EffectGenerator, async_run, default_handlers, do
from doeff.effects.cache import CacheGetEffect, CachePutEffect

structured_llm_module = importlib.import_module("doeff_gemini.structured_llm")


async def _run_with_default_cost(program: Any, *, env: dict[str, Any] | None = None):
    return await async_run(
        program,
        handlers=[default_gemini_cost_handler, *default_handlers()],
        env=env,
    )


class _InMemoryTTLCache:
    def __init__(self) -> None:
        self.now = 0.0
        self.entries: dict[Any, tuple[Any, float | None]] = {}
        self.get_keys: list[Any] = []
        self.put_effects: list[CachePutEffect] = []

    def make_handler(self):
        cache = self

        @do
        def handler(effect: CacheGetEffect | CachePutEffect, k: Any) -> EffectGenerator[Any]:
            if isinstance(effect, CacheGetEffect):
                cache.get_keys.append(effect.key)
                entry = cache.entries.get(effect.key)
                if entry is None:
                    raise KeyError(effect.key)
                value, expires_at = entry
                if expires_at is not None and cache.now >= expires_at:
                    cache.entries.pop(effect.key, None)
                    raise KeyError(effect.key)
                from doeff import Resume

                return (yield Resume(k, value))

            if isinstance(effect, CachePutEffect):
                cache.put_effects.append(effect)
                ttl = effect.policy.ttl
                expires_at = None if ttl is None else cache.now + ttl
                cache.entries[effect.key] = (effect.value, expires_at)
                from doeff import Resume

                return (yield Resume(k, None))

        return handler


async def _run_with_cache(program: Any, cache: _InMemoryTTLCache, *, env: dict[str, Any] | None = None):
    from doeff import WithHandler

    return await _run_with_default_cost(WithHandler(cache.make_handler(), program), env=env)


def _extract_file_uri(part: Any) -> str | None:
    file_data = getattr(part, "file_data", None)
    if file_data is None and isinstance(part, dict):
        file_data = part.get("file_data")
    if file_data is None:
        return None
    if isinstance(file_data, dict):
        value = file_data.get("file_uri")
        return str(value) if value else None
    value = getattr(file_data, "file_uri", None)
    return str(value) if value else None


class SimpleResponse(BaseModel):
    answer: str
    confidence: float


class ComplexResponse(BaseModel):
    title: str
    items: list[str]
    metadata: dict[str, Any]


class ScoreWithReasoning(BaseModel):
    symbol: str
    score: float
    reasoning: str


class ScoreWithReasoningV2(BaseModel):
    symbol: str
    score: float
    reasoning: str


class SymbolAssessmentsV2(BaseModel):
    assessments: list[ScoreWithReasoning]


def test_gemini_random_backoff_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Random backoff should expand the upper bound exponentially."""

    recorded_ranges: list[tuple[float, float]] = []

    def fake_uniform(low: float, high: float) -> float:
        recorded_ranges.append((low, high))
        return high

    monkeypatch.setattr(
        structured_llm_module,
        "random",
        SimpleNamespace(uniform=fake_uniform),
    )

    first_delay = structured_llm_module._gemini_random_backoff(1, None)
    third_delay = structured_llm_module._gemini_random_backoff(3, None)

    assert recorded_ranges[0] == (1.0, 1.0)
    assert recorded_ranges[1] == (1.0, 4.0)
    assert first_delay == 1.0
    assert third_delay == 4.0


def test_image_edit_entrypoint_alias() -> None:
    """Expose image_edit__gemini as an alias to edit_image__gemini."""

    assert image_edit__gemini is edit_image__gemini


@pytest.mark.asyncio
async def test_build_contents_text_only() -> None:
    """Ensure text-only prompts become a single user content block."""

    @do
    def flow() -> EffectGenerator[Any]:
        contents = yield build_contents("Hello Gemini")
        return contents

    result = await _run_with_default_cost(flow())

    assert result.is_ok()
    contents = result.value
    assert len(contents) == 1
    content = contents[0]

    assert isinstance(content, genai_types.Content)
    assert content.role == "user"
    assert len(content.parts) == 1
    assert content.parts[0].text == "Hello Gemini"


@pytest.mark.asyncio
async def test_build_contents_uploads_local_file_and_caches_result(tmp_path: Path) -> None:
    """Local files should auto-upload and be persisted via CachePut with 48h TTL."""

    local_file = tmp_path / "clip.mp4"
    local_file.write_bytes(b"fake-video")

    uploaded_uri = "https://generativelanguage.googleapis.com/v1beta/files/abc"
    async_files = SimpleNamespace(
        upload=AsyncMock(
            return_value=SimpleNamespace(
                name="files/abc",
                uri=uploaded_uri,
                state="PROCESSING",
                mime_type="video/mp4",
            )
        ),
        get=AsyncMock(
            return_value=SimpleNamespace(
                name="files/abc",
                uri=uploaded_uri,
                state="ACTIVE",
                mime_type="video/mp4",
            )
        ),
    )
    client = SimpleNamespace(async_client=SimpleNamespace(files=async_files))
    cache = _InMemoryTTLCache()

    @do
    def flow() -> EffectGenerator[Any]:
        return (
            yield build_contents(
                text="Summarize this clip",
                content_parts=[
                    {
                        "type": "video",
                        "local_path": local_file.as_posix(),
                        "mime_type": "video/mp4",
                    }
                ],
            )
        )

    result = await _run_with_cache(flow(), cache, env={"gemini_client": client})

    assert result.is_ok()
    contents = result.value
    assert len(contents) == 1
    part = contents[0].parts[0]
    assert _extract_file_uri(part) == uploaded_uri

    async_files.upload.assert_awaited_once()
    async_files.get.assert_awaited_once()

    assert cache.put_effects
    assert cache.put_effects[0].policy.ttl == 172800
    assert cache.put_effects[0].policy.lifecycle.value == "persistent"
    assert cache.put_effects[0].policy.resolved_storage().value == "disk"


@pytest.mark.asyncio
async def test_build_contents_reuses_active_cached_upload(tmp_path: Path) -> None:
    """Cache hit should reuse URI directly and skip upload/refresh network calls."""

    local_file = tmp_path / "clip.mp4"
    local_file.write_bytes(b"fake-video")

    cache = _InMemoryTTLCache()
    cache_key = structured_llm_module._gemini_file_cache_key(local_file.as_posix())
    cache.entries[cache_key] = (
        {
            "name": "files/cached",
            "uri": "https://old.example/files/cached",
            "mime_type": "video/mp4",
        },
        None,
    )

    cached_uri = "https://old.example/files/cached"
    async_files = SimpleNamespace(
        upload=AsyncMock(),
        get=AsyncMock(
            return_value=SimpleNamespace(
                name="files/cached",
                uri="https://new.example/files/cached",
                state="ACTIVE",
                mime_type="video/mp4",
            )
        ),
    )
    client = SimpleNamespace(async_client=SimpleNamespace(files=async_files))

    @do
    def flow() -> EffectGenerator[Any]:
        return (
            yield build_contents(
                text="Reuse",
                content_parts=[
                    {
                        "type": "video",
                        "local_path": local_file.as_posix(),
                        "mime_type": "video/mp4",
                    }
                ],
            )
        )

    result = await _run_with_cache(flow(), cache, env={"gemini_client": client})

    assert result.is_ok()
    contents = result.value
    assert _extract_file_uri(contents[0].parts[0]) == cached_uri
    async_files.upload.assert_not_called()
    async_files.get.assert_not_called()


@pytest.mark.asyncio
async def test_build_contents_reuploads_when_cache_entry_expired(tmp_path: Path) -> None:
    """Expired cache entry should trigger a new upload."""

    local_file = tmp_path / "clip.mp4"
    local_file.write_bytes(b"fake-video")

    upload_uris = [
        "https://example.com/files/first",
        "https://example.com/files/second",
    ]
    async_files = SimpleNamespace(
        upload=AsyncMock(
            side_effect=[
                SimpleNamespace(
                    name="files/first",
                    uri=upload_uris[0],
                    state="PROCESSING",
                    mime_type="video/mp4",
                ),
                SimpleNamespace(
                    name="files/second",
                    uri=upload_uris[1],
                    state="PROCESSING",
                    mime_type="video/mp4",
                ),
            ]
        ),
        get=AsyncMock(
            side_effect=[
                SimpleNamespace(
                    name="files/first",
                    uri=upload_uris[0],
                    state="ACTIVE",
                    mime_type="video/mp4",
                ),
                SimpleNamespace(
                    name="files/second",
                    uri=upload_uris[1],
                    state="ACTIVE",
                    mime_type="video/mp4",
                ),
            ]
        ),
    )
    client = SimpleNamespace(async_client=SimpleNamespace(files=async_files))
    cache = _InMemoryTTLCache()

    @do
    def flow() -> EffectGenerator[Any]:
        return (
            yield build_contents(
                text="Summarize",
                content_parts=[
                    {
                        "type": "video",
                        "local_path": local_file.as_posix(),
                        "mime_type": "video/mp4",
                    }
                ],
            )
        )

    first = await _run_with_cache(flow(), cache, env={"gemini_client": client})
    assert first.is_ok()
    assert _extract_file_uri(first.value[0].parts[0]) == upload_uris[0]

    cache.now += 172801.0

    second = await _run_with_cache(flow(), cache, env={"gemini_client": client})
    assert second.is_ok()
    assert _extract_file_uri(second.value[0].parts[0]) == upload_uris[1]

    assert async_files.upload.await_count == 2


@pytest.mark.asyncio
async def test_build_contents_reuploads_when_file_signature_changes(tmp_path: Path) -> None:
    """Changed mtime/size should produce a new cache key and trigger re-upload."""

    local_file = tmp_path / "clip.mp4"
    local_file.write_bytes(b"first-version")

    upload_uris = [
        "https://example.com/files/v1",
        "https://example.com/files/v2",
    ]
    async_files = SimpleNamespace(
        upload=AsyncMock(
            side_effect=[
                SimpleNamespace(
                    name="files/v1",
                    uri=upload_uris[0],
                    state="PROCESSING",
                    mime_type="video/mp4",
                ),
                SimpleNamespace(
                    name="files/v2",
                    uri=upload_uris[1],
                    state="PROCESSING",
                    mime_type="video/mp4",
                ),
            ]
        ),
        get=AsyncMock(
            side_effect=[
                SimpleNamespace(
                    name="files/v1",
                    uri=upload_uris[0],
                    state="ACTIVE",
                    mime_type="video/mp4",
                ),
                SimpleNamespace(
                    name="files/v2",
                    uri=upload_uris[1],
                    state="ACTIVE",
                    mime_type="video/mp4",
                ),
            ]
        ),
    )
    client = SimpleNamespace(async_client=SimpleNamespace(files=async_files))
    cache = _InMemoryTTLCache()

    @do
    def flow() -> EffectGenerator[Any]:
        return (
            yield build_contents(
                text="Summarize",
                content_parts=[
                    {
                        "type": "video",
                        "local_path": local_file.as_posix(),
                        "mime_type": "video/mp4",
                    }
                ],
            )
        )

    first = await _run_with_cache(flow(), cache, env={"gemini_client": client})
    assert first.is_ok()
    assert _extract_file_uri(first.value[0].parts[0]) == upload_uris[0]

    local_file.write_bytes(b"second-version-with-different-size")
    stat = local_file.stat()
    os.utime(local_file, (stat.st_atime + 1, stat.st_mtime + 1))

    second = await _run_with_cache(flow(), cache, env={"gemini_client": client})
    assert second.is_ok()
    assert _extract_file_uri(second.value[0].parts[0]) == upload_uris[1]

    assert async_files.upload.await_count == 2
    assert len(cache.get_keys) >= 2
    assert cache.get_keys[0] != cache.get_keys[1]


@pytest.mark.asyncio
async def test_build_contents_passes_through_https_uri_without_upload() -> None:
    """HTTPS URIs should pass through untouched and avoid File API upload."""

    async_files = SimpleNamespace(upload=AsyncMock(), get=AsyncMock())
    client = SimpleNamespace(async_client=SimpleNamespace(files=async_files))

    @do
    def flow() -> EffectGenerator[Any]:
        return (
            yield build_contents(
                text="Summarize",
                content_parts=[
                    {
                        "type": "video",
                        "file_uri": "https://example.com/media/video.mp4",
                        "mime_type": "video/mp4",
                    }
                ],
            )
        )

    result = await _run_with_default_cost(flow(), env={"gemini_client": client})

    assert result.is_ok()
    part = result.value[0].parts[0]
    assert _extract_file_uri(part) == "https://example.com/media/video.mp4"
    async_files.upload.assert_not_called()
    async_files.get.assert_not_called()


@pytest.mark.asyncio
async def test_build_generation_config_basic() -> None:
    """Configuration builder should populate common fields."""

    @do
    def flow() -> EffectGenerator[Any]:
        config = yield build_generation_config(
            temperature=0.4,
            max_output_tokens=512,
            top_p=0.95,
            top_k=32,
            candidate_count=2,
            system_instruction="Be concise",
            safety_settings=[
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_LOW_AND_ABOVE"}
            ],
            tools=None,
            tool_config=None,
            response_format=None,
            generation_config_overrides={"stop_sequences": ["END"], "logprobs": 2},
        )
        return config

    result = await _run_with_default_cost(flow())

    assert result.is_ok()
    config = result.value
    assert config.temperature == 0.4
    assert config.max_output_tokens == 512
    assert config.top_p == 0.95
    assert config.top_k == 32
    assert config.candidate_count == 2
    assert config.system_instruction == "Be concise"
    assert config.stop_sequences == ["END"]
    assert config.logprobs == 2


@pytest.mark.asyncio
async def test_build_generation_config_with_modalities() -> None:
    """Response modalities should be captured when provided."""

    @do
    def flow() -> EffectGenerator[Any]:
        return (
            yield build_generation_config(
                temperature=0.7,
                max_output_tokens=1024,
                top_p=None,
                top_k=None,
                candidate_count=1,
                system_instruction=None,
                safety_settings=None,
                tools=None,
                tool_config=None,
                response_format=None,
                response_modalities=["TEXT", "IMAGE"],
                generation_config_overrides=None,
                image_config=None,
            )
        )

    result = await _run_with_default_cost(flow())

    assert result.is_ok()
    config = result.value
    assert config.response_modalities == ["TEXT", "IMAGE"]


@pytest.mark.asyncio
async def test_process_structured_response_from_text() -> None:
    """Structured responses should parse JSON text when no parsed payload is present."""

    response = SimpleNamespace(
        parsed=None,
        text=json.dumps({"answer": "42", "confidence": 0.9}),
        candidates=None,
    )

    @do
    def flow() -> EffectGenerator[SimpleResponse]:
        result = yield process_structured_response(response, SimpleResponse)
        return result

    result = await _run_with_default_cost(flow())

    assert result.is_ok()
    parsed = result.value
    assert isinstance(parsed, SimpleResponse)
    assert parsed.answer == "42"
    assert parsed.confidence == 0.9


@pytest.mark.asyncio
async def test_process_structured_response_from_parsed() -> None:
    """When Gemini provides a parsed payload it should be reused directly."""

    parsed_response = SimpleResponse(answer="4", confidence=1.0)
    response = SimpleNamespace(parsed=[parsed_response])

    @do
    def flow() -> EffectGenerator[SimpleResponse]:
        result = yield process_structured_response(response, SimpleResponse)
        return result

    result = await _run_with_default_cost(flow())

    assert result.is_ok()
    assert result.value is parsed_response


@pytest.mark.asyncio
async def test_process_structured_response_nested_model_from_parsed() -> None:
    """Nested Pydantic structures should be reused when provided in parsed payload."""

    parsed_response = SymbolAssessmentsV2(
        assessments=[
            ScoreWithReasoning(symbol="MSFT", score=0.88, reasoning="High relevance"),
            ScoreWithReasoning(symbol="GOOG", score=0.73, reasoning="Moderate relevance"),
        ]
    )
    response = SimpleNamespace(parsed=[parsed_response])

    @do
    def flow() -> EffectGenerator[SymbolAssessmentsV2]:
        result = yield process_structured_response(response, SymbolAssessmentsV2)
        return result

    result = await _run_with_default_cost(flow())

    assert result.is_ok()
    assert result.value is parsed_response
    assert len(result.value.assessments) == 2


@pytest.mark.asyncio
async def test_process_structured_response_from_json_part() -> None:
    """Structured responses should leverage JSON parts when available."""

    json_part = SimpleNamespace(json={"answer": "42", "confidence": 0.75})
    response = SimpleNamespace(
        parsed=None,
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=[json_part]),
                contents=None,
            )
        ],
        text="",
    )

    @do
    def flow() -> EffectGenerator[SimpleResponse]:
        result = yield process_structured_response(response, SimpleResponse)
        return result

    result = await _run_with_default_cost(flow())

    assert result.is_ok()
    payload = result.value
    assert isinstance(payload, SimpleResponse)
    assert payload.answer == "42"
    assert math.isclose(payload.confidence, 0.75)


@pytest.mark.asyncio
async def test_process_structured_response_from_json_part_string_payload() -> None:
    """String JSON payloads should be parsed after trimming whitespace."""

    json_part = SimpleNamespace(json='  {\n    "answer": "84", \n    "confidence": 0.55\n}  ')
    response = SimpleNamespace(
        parsed=None,
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=[json_part]),
                contents=None,
            )
        ],
        text="",
    )

    @do
    def flow() -> EffectGenerator[SimpleResponse]:
        result = yield process_structured_response(response, SimpleResponse)
        return result

    result = await _run_with_default_cost(flow())

    assert result.is_ok()
    payload = result.value
    assert payload.answer == "84"
    assert math.isclose(payload.confidence, 0.55)


@pytest.mark.asyncio
async def test_process_structured_response_from_json_part_with_model() -> None:
    """BaseModel payloads embedded in JSON parts should be converted to dicts."""

    json_part = SimpleNamespace(json=SimpleResponse(answer="128", confidence=0.33))
    response = SimpleNamespace(
        parsed=None,
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=[json_part]),
                contents=None,
            )
        ],
        text="",
    )

    @do
    def flow() -> EffectGenerator[SimpleResponse]:
        result = yield process_structured_response(response, SimpleResponse)
        return result

    result = await _run_with_default_cost(flow())

    assert result.is_ok()
    payload = result.value
    assert payload.answer == "128"
    assert math.isclose(payload.confidence, 0.33)


@pytest.mark.asyncio
async def test_process_structured_response_nested_model_from_json_part() -> None:
    """Nested Pydantic structures should parse when provided as JSON parts."""

    json_part = SimpleNamespace(
        json={
            "assessments": [
                {"symbol": "AAPL", "score": 0.91, "reasoning": "Strong fundamentals"},
                {"symbol": "TSLA", "score": 0.52, "reasoning": "Volatile"},
            ]
        }
    )
    response = SimpleNamespace(
        parsed=None,
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[json_part]), contents=None)],
        text="",
    )

    @do
    def flow() -> EffectGenerator[SymbolAssessmentsV2]:
        result = yield process_structured_response(response, SymbolAssessmentsV2)
        return result

    result = await _run_with_default_cost(flow())

    assert result.is_ok()
    payload = result.value
    assert isinstance(payload, SymbolAssessmentsV2)
    assert payload.assessments[0].symbol == "AAPL"
    assert math.isclose(payload.assessments[1].score, 0.52)


@pytest.mark.asyncio
async def test_process_structured_response_from_outputs_structure() -> None:
    """JSON payloads nested under outputs/contents should be discovered."""

    response = SimpleNamespace(
        parsed=None,
        outputs=[
            SimpleNamespace(
                contents=[
                    SimpleNamespace(
                        parts=[SimpleNamespace(json={"answer": "11", "confidence": 0.61})]
                    )
                ]
            )
        ],
        candidates=None,
        text="",
    )

    @do
    def flow() -> EffectGenerator[SimpleResponse]:
        result = yield process_structured_response(response, SimpleResponse)
        return result

    result = await _run_with_default_cost(flow())

    assert result.is_ok()
    payload = result.value
    assert payload.answer == "11"
    assert math.isclose(payload.confidence, 0.61)


@pytest.mark.asyncio
async def test_process_structured_response_without_json_payload() -> None:
    """Missing JSON payload should fail with a ValueError, not JSONDecodeError."""

    response = SimpleNamespace(parsed=None, candidates=[], text="No structured data returned")

    @do
    def flow() -> EffectGenerator[SimpleResponse]:
        result = yield process_structured_response(response, SimpleResponse)
        return result

    result = await _run_with_default_cost(flow())

    assert result.is_err()
    error = result.result.error
    assert isinstance(error, GeminiStructuredOutputError)
    assert error.format_name.endswith("SimpleResponse")


@pytest.mark.asyncio
async def test_process_unstructured_response() -> None:
    """Unstructured responses surface plain text."""

    response = SimpleNamespace(text="A concise answer")

    @do
    def flow() -> EffectGenerator[str]:
        result = yield process_unstructured_response(response)
        return result

    result = await _run_with_default_cost(flow())

    assert result.is_ok()
    assert result.value == "A concise answer"


@pytest.mark.asyncio
async def test_repair_structured_response_uses_default_when_missing(monkeypatch) -> None:
    """Fallback should call the Gemini-backed repair when no custom SLLM is provided."""

    calls: list[dict[str, Any]] = []

    @do
    def fake_gemini_json_fix(
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
        calls.append(
            {
                "model": model,
                "content": malformed_content,
                "max_output_tokens": max_output_tokens,
            }
        )
        return response_format(answer="fixed", confidence=0.5)

    monkeypatch.setattr(structured_llm_module, "_gemini_json_fix", fake_gemini_json_fix)

    malformed = json.dumps({"answer": "???", "confidence": 0.0})

    @do
    def flow() -> EffectGenerator[SimpleResponse]:
        default_sllm_impl = structured_llm_module._make_gemini_json_fix_sllm(
            model="gemini-default",
            max_output_tokens=128,
            system_instruction=None,
            safety_settings=None,
            tools=None,
            tool_config=None,
            generation_config_overrides=None,
        )

        def default_sllm(json_text: str, response_format: type[BaseModel]):
            return default_sllm_impl(json_text, response_format=response_format)

        return (
            yield structured_llm_module.repair_structured_response(
                model="gemini-default",
                response_format=SimpleResponse,
                malformed_content=malformed,
                max_output_tokens=128,
                default_sllm=default_sllm,
            )
        )

    result = await _run_with_default_cost(flow())

    assert result.is_ok()
    assert result.value.answer == "fixed"
    assert calls
    assert calls[0]["model"] == "gemini-default"


@pytest.mark.asyncio
async def test_repair_structured_response_uses_injected_sllm() -> None:
    """Custom SLLM provided via injection should replace the Gemini fallback."""

    malformed = json.dumps({"answer": "bad", "confidence": 0.1})
    custom_called = {"value": False}

    @do
    def custom_fix(json_text: str, response_format: type[BaseModel]) -> EffectGenerator[Any]:
        custom_called["value"] = True
        data = json.loads(json_text)
        data["answer"] = "repaired"
        data["confidence"] = 0.99
        return response_format(**data)

    @do
    def flow() -> EffectGenerator[SimpleResponse]:
        default_sllm_impl = structured_llm_module._make_gemini_json_fix_sllm(
            model="unused",
            max_output_tokens=64,
            system_instruction=None,
            safety_settings=None,
            tools=None,
            tool_config=None,
            generation_config_overrides=None,
        )

        def default_sllm(json_text: str, response_format: type[BaseModel]):
            return default_sllm_impl(json_text, response_format=response_format)

        return (
            yield structured_llm_module.repair_structured_response(
                model="unused",
                response_format=SimpleResponse,
                malformed_content=malformed,
                max_output_tokens=64,
                default_sllm=default_sllm,
            )
        )

    result = await _run_with_default_cost(
        flow(),
        env={
            "sllm_for_json_fix": (
                lambda json_text, response_format: custom_fix(
                    json_text, response_format=response_format
                )
            )
        },
    )

    assert result.is_ok()
    payload = result.value
    assert payload.answer == "repaired"
    assert math.isclose(payload.confidence, 0.99)
    assert custom_called["value"] is True


@pytest.mark.asyncio
async def test_structured_llm_text_only() -> None:
    """End-to-end call should delegate to the async Gemini client."""

    mock_response = SimpleNamespace(
        text="Test response",
        usage_metadata=SimpleNamespace(
            input_token_count=10,
            output_token_count=20,
            total_token_count=30,
        ),
    )

    async_models = SimpleNamespace(generate_content=AsyncMock(return_value=mock_response))
    async_client = SimpleNamespace(models=async_models)
    mock_client = SimpleNamespace(async_client=async_client)

    @do
    def flow() -> EffectGenerator[str]:
        result = yield structured_llm__gemini(
            text="Hello Gemini",
            model="gemini-1.5-flash",
            max_output_tokens=128,
            temperature=0.2,
        )
        return result

    result = await _run_with_default_cost(flow(), env={"gemini_client": mock_client})

    assert result.is_ok()
    assert result.value == "Test response"

    async_models.generate_content.assert_called_once()
    call_kwargs = async_models.generate_content.call_args.kwargs
    assert call_kwargs["model"] == "gemini-1.5-flash"
    config = call_kwargs["config"]
    assert config.max_output_tokens == 128
    assert config.temperature == 0.2

    api_calls = result.raw_store.get("gemini_api_calls")
    assert api_calls is not None
    assert api_calls[0]["prompt_text"] == "Hello Gemini"
    assert api_calls[0]["prompt_images"] == []


@pytest.mark.asyncio
async def test_structured_llm_with_pydantic() -> None:
    """Structured output should return the requested Pydantic model."""

    parsed_response = SimpleResponse(answer="4", confidence=0.99)
    mock_response = SimpleNamespace(
        parsed=[parsed_response],
        text=json.dumps(parsed_response.model_dump()),
        usage_metadata=None,
    )

    async_models = SimpleNamespace(generate_content=AsyncMock(return_value=mock_response))
    async_client = SimpleNamespace(models=async_models)
    mock_client = SimpleNamespace(async_client=async_client)

    @do
    def flow() -> EffectGenerator[SimpleResponse]:
        result = yield structured_llm__gemini(
            text="What is 2+2?",
            model="gemini-1.5-pro",
            response_format=SimpleResponse,
        )
        return result

    result = await _run_with_default_cost(flow(), env={"gemini_client": mock_client})

    assert result.is_ok()
    value = result.value
    assert isinstance(value, SimpleResponse)
    assert value.answer == "4"
    assert value.confidence == 0.99

    async_models.generate_content.assert_called_once()
    config = async_models.generate_content.call_args.kwargs["config"]
    assert config.response_schema is SimpleResponse
    assert config.response_mime_type == "application/json"


class _InlineData:
    def __init__(self, data: bytes, mime_type: str) -> None:
        self.data = data
        self.mime_type = mime_type


class _ContentPart:
    def __init__(self, text: str | None = None, inline_data: Any | None = None) -> None:
        self.text = text
        self.inline_data = inline_data


class _CandidateContent:
    def __init__(self, parts: list[Any]) -> None:
        self.parts = parts


@pytest.mark.asyncio
async def test_process_image_edit_response_success(tmp_path: Path) -> None:
    """Image edit response should surface image bytes and optional text."""

    base_image = Image.new("RGB", (1, 1), color=(0, 255, 0))
    buffer = BytesIO()
    base_image.save(buffer, format="PNG")
    image_bytes = buffer.getvalue()
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=_CandidateContent(
                    parts=[
                        _ContentPart(text="Edit applied"),
                        _ContentPart(
                            inline_data=_InlineData(data=image_bytes, mime_type="image/png")
                        ),
                    ]
                )
            )
        ]
    )

    @do
    def flow() -> EffectGenerator[GeminiImageEditResult]:
        return (yield process_image_edit_response(response))

    result = await _run_with_default_cost(flow())

    assert result.is_ok()
    payload = result.value
    assert payload.image_bytes == image_bytes
    assert payload.mime_type == "image/png"
    assert payload.text == "Edit applied"
    pil_image = payload.to_pil_image()
    assert pil_image.size == (1, 1)
    output_path = tmp_path / "edited.png"
    payload.save(output_path.as_posix())
    assert output_path.exists()


@pytest.mark.asyncio
async def test_edit_image__gemini_success() -> None:
    """End-to-end image edit call should capture inline image data."""

    base_image = Image.new("RGB", (8, 4), color=(255, 0, 0))
    buffer = BytesIO()
    base_image.save(buffer, format="PNG")
    uploaded = buffer.getvalue()

    edited_image = Image.new("RGB", (4, 4), color=(0, 128, 0))
    edited_buffer = BytesIO()
    edited_image.save(edited_buffer, format="PNG")
    edited_bytes = edited_buffer.getvalue()
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=_CandidateContent(
                    parts=[
                        _ContentPart(text="Success"),
                        _ContentPart(
                            inline_data=_InlineData(data=edited_bytes, mime_type="image/png")
                        ),
                    ]
                )
            )
        ],
        usage_metadata=SimpleNamespace(
            text_input_token_count=100,
            text_output_token_count=10,
            image_input_token_count=1,
            image_output_token_count=1,
            total_token_count=112,
        ),
    )

    async_models = SimpleNamespace(generate_content=AsyncMock(return_value=response))
    async_client = SimpleNamespace(models=async_models)
    client = SimpleNamespace(async_client=async_client)

    @do
    def flow() -> EffectGenerator[GeminiImageEditResult]:
        return (
            yield edit_image__gemini(
                prompt="Enhance the colors",
                model="gemini-2.5-flash-image-preview",
                images=[Image.open(BytesIO(uploaded))],
                temperature=0.5,
                top_k=8,
                aspect_ratio="16:9",
                image_size="2K",
            )
        )

    result = await _run_with_default_cost(flow(), env={"gemini_client": client})

    assert result.is_ok()
    payload = result.value
    assert payload.image_bytes == edited_bytes
    assert payload.mime_type == "image/png"
    assert payload.text == "Success"

    async_models.generate_content.assert_called_once()
    call_kwargs = async_models.generate_content.call_args.kwargs
    config = call_kwargs["config"]
    assert config.response_modalities == ["TEXT", "IMAGE"]

    api_calls = result.raw_store.get("gemini_api_calls")
    assert api_calls is not None
    assert api_calls[0]["prompt_text"] == "Enhance the colors"
    assert api_calls[0]["prompt_images"][0]["mime_type"].startswith("image/")


@pytest.mark.asyncio
async def test_track_api_call_accumulates_under_gather() -> None:
    """Atomic updates should preserve Gemini stats across parallel calls."""

    model = "gemini-1.5-flash"
    call_defs = [
        {"request_id": "req-1", "prompt": "First", "input": 1200, "output": 640},
        {"request_id": "req-2", "prompt": "Second", "input": 800, "output": 320},
    ]

    @do
    def invoke(call: dict[str, Any]) -> EffectGenerator[APICallMetadata]:
        response = SimpleNamespace(
            text="ok",
            response_id=call["request_id"],
            usage_metadata=SimpleNamespace(
                text_input_token_count=call["input"],
                text_output_token_count=call["output"],
                total_token_count=call["input"] + call["output"],
            ),
        )
        start_time = time.time() - 0.01
        return (
            yield track_api_call(
                operation="generate",
                model=model,
                request_summary={"prompt": call["prompt"]},
                request_payload={"text": call["prompt"], "images": []},
                response=response,
                start_time=start_time,
                error=None,
            )
        )

    @do
    def run_calls() -> EffectGenerator[list[APICallMetadata]]:
        first = yield invoke(call_defs[0])
        second = yield invoke(call_defs[1])
        return [first, second]

    result = await _run_with_default_cost(run_calls())

    assert result.is_ok()
    state = result.raw_store
    api_calls = state.get("gemini_api_calls")
    assert api_calls is not None
    assert sorted(entry["request_id"] for entry in api_calls) == [
        "req-1",
        "req-2",
    ]

    expected_total = sum(
        calculate_cost(
            model,
            {
                "text_input_tokens": call["input"],
                "text_output_tokens": call["output"],
            },
        ).total_cost
        for call in call_defs
    )

    assert math.isclose(state.get("gemini_total_cost", 0.0), expected_total, rel_tol=1e-9)
    assert math.isclose(state.get(f"gemini_cost_{model}", 0.0), expected_total, rel_tol=1e-9)
