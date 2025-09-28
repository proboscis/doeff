"""Tests for the Gemini structured LLM implementation."""

import json
import math
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image
from doeff_gemini import (
    GeminiImageEditResult,
    build_contents,
    build_generation_config,
    edit_image__gemini,
    process_image_edit_response,
    process_structured_response,
    process_unstructured_response,
    structured_llm__gemini,
)
from doeff_gemini.structured_llm import GeminiStructuredOutputError
from doeff_gemini.costs import calculate_cost
from doeff_gemini.client import track_api_call
from doeff_gemini.types import APICallMetadata
from google.genai import types as genai_types
from pydantic import BaseModel

from doeff import EffectGenerator, ExecutionContext, Gather, ProgramInterpreter, do


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


@pytest.mark.asyncio
async def test_build_contents_text_only() -> None:
    """Ensure text-only prompts become a single user content block."""

    @do
    def flow() -> EffectGenerator[Any]:
        contents = yield build_contents("Hello Gemini")
        return contents

    engine = ProgramInterpreter()
    result = await engine.run(flow())

    assert result.is_ok
    contents = result.value
    assert len(contents) == 1
    content = contents[0]

    assert isinstance(content, genai_types.Content)
    assert content.role == "user"
    assert len(content.parts) == 1
    assert content.parts[0].text == "Hello Gemini"


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
            safety_settings=[{"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_LOW_AND_ABOVE"}],
            tools=None,
            tool_config=None,
            response_format=None,
            generation_config_overrides={"stop_sequences": ["END"], "logprobs": 2},
        )
        return config

    engine = ProgramInterpreter()
    result = await engine.run(flow())

    assert result.is_ok
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
            )
        )

    engine = ProgramInterpreter()
    result = await engine.run(flow())

    assert result.is_ok
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

    engine = ProgramInterpreter()
    result = await engine.run(flow())

    assert result.is_ok
    parsed = result.value
    assert isinstance(parsed, SimpleResponse)
    assert parsed.answer == "42"
    assert parsed.confidence == 0.9


@pytest.mark.asyncio
async def test_process_structured_response_from_parsed() -> None:
    """When Gemini provides a parsed payload it should be reused directly."""

    parsed_response = SimpleResponse(answer="4", confidence=1.0)
    response = MagicMock()
    response.parsed = [parsed_response]

    @do
    def flow() -> EffectGenerator[SimpleResponse]:
        result = yield process_structured_response(response, SimpleResponse)
        return result

    engine = ProgramInterpreter()
    result = await engine.run(flow())

    assert result.is_ok
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

    engine = ProgramInterpreter()
    result = await engine.run(flow())

    assert result.is_ok
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

    engine = ProgramInterpreter()
    result = await engine.run(flow())

    assert result.is_ok
    payload = result.value
    assert isinstance(payload, SimpleResponse)
    assert payload.answer == "42"
    assert math.isclose(payload.confidence, 0.75)


@pytest.mark.asyncio
async def test_process_structured_response_from_json_part_string_payload() -> None:
    """String JSON payloads should be parsed after trimming whitespace."""

    json_part = SimpleNamespace(json="  {\n    \"answer\": \"84\", \n    \"confidence\": 0.55\n}  ")
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

    engine = ProgramInterpreter()
    result = await engine.run(flow())

    assert result.is_ok
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

    engine = ProgramInterpreter()
    result = await engine.run(flow())

    assert result.is_ok
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
        candidates=[
            SimpleNamespace(content=SimpleNamespace(parts=[json_part]), contents=None)
        ],
        text="",
    )

    @do
    def flow() -> EffectGenerator[SymbolAssessmentsV2]:
        result = yield process_structured_response(response, SymbolAssessmentsV2)
        return result

    engine = ProgramInterpreter()
    result = await engine.run(flow())

    assert result.is_ok
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

    engine = ProgramInterpreter()
    result = await engine.run(flow())

    assert result.is_ok
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

    engine = ProgramInterpreter()
    result = await engine.run(flow())

    assert result.is_err
    error = result.result.error
    assert isinstance(error, GeminiStructuredOutputError)
    assert error.format_name.endswith("SimpleResponse")


@pytest.mark.asyncio
async def test_process_unstructured_response() -> None:
    """Unstructured responses surface plain text."""

    response = MagicMock()
    response.text = "A concise answer"

    @do
    def flow() -> EffectGenerator[str]:
        result = yield process_unstructured_response(response)
        return result

    engine = ProgramInterpreter()
    result = await engine.run(flow())

    assert result.is_ok
    assert result.value == "A concise answer"


@pytest.mark.asyncio
async def test_structured_llm_text_only() -> None:
    """End-to-end call should delegate to the async Gemini client."""

    mock_response = MagicMock()
    mock_response.text = "Test response"
    usage = MagicMock()
    usage.input_token_count = 10
    usage.output_token_count = 20
    usage.total_token_count = 30
    mock_response.usage_metadata = usage

    async_models = MagicMock()
    async_models.generate_content = AsyncMock(return_value=mock_response)
    async_client = MagicMock()
    async_client.models = async_models

    mock_client = MagicMock()
    mock_client.async_client = async_client

    @do
    def flow() -> EffectGenerator[str]:
        result = yield structured_llm__gemini(
            text="Hello Gemini",
            model="gemini-1.5-flash",
            max_output_tokens=128,
            temperature=0.2,
        )
        return result

    engine = ProgramInterpreter()
    context = ExecutionContext(env={"gemini_client": mock_client})
    result = await engine.run(flow(), context)

    assert result.is_ok
    assert result.value == "Test response"

    async_models.generate_content.assert_called_once()
    call_kwargs = async_models.generate_content.call_args.kwargs
    assert call_kwargs["model"] == "gemini-1.5-flash"
    config = call_kwargs["config"]
    assert config.max_output_tokens == 128
    assert config.temperature == 0.2

    api_calls = result.context.state.get("gemini_api_calls")
    assert api_calls is not None
    assert api_calls[0]["prompt_text"] == "Hello Gemini"
    assert api_calls[0]["prompt_images"] == []


@pytest.mark.asyncio
async def test_structured_llm_with_pydantic() -> None:
    """Structured output should return the requested Pydantic model."""

    parsed_response = SimpleResponse(answer="4", confidence=0.99)
    mock_response = MagicMock()
    mock_response.parsed = [parsed_response]
    mock_response.text = json.dumps(parsed_response.model_dump())
    mock_response.usage_metadata = None

    async_models = MagicMock()
    async_models.generate_content = AsyncMock(return_value=mock_response)
    async_client = MagicMock()
    async_client.models = async_models
    mock_client = MagicMock()
    mock_client.async_client = async_client

    @do
    def flow() -> EffectGenerator[SimpleResponse]:
        result = yield structured_llm__gemini(
            text="What is 2+2?",
            model="gemini-1.5-pro",
            response_format=SimpleResponse,
        )
        return result

    engine = ProgramInterpreter()
    context = ExecutionContext(env={"gemini_client": mock_client})
    result = await engine.run(flow(), context)

    assert result.is_ok
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
                        _ContentPart(inline_data=_InlineData(data=image_bytes, mime_type="image/png")),
                    ]
                )
            )
        ]
    )

    @do
    def flow() -> EffectGenerator[GeminiImageEditResult]:
        return (yield process_image_edit_response(response))

    engine = ProgramInterpreter()
    result = await engine.run(flow())

    assert result.is_ok
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
                        _ContentPart(inline_data=_InlineData(data=edited_bytes, mime_type="image/png")),
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

    async_models = MagicMock()
    async_models.generate_content = AsyncMock(return_value=response)
    async_client = MagicMock()
    async_client.models = async_models

    client = MagicMock()
    client.async_client = async_client

    @do
    def flow() -> EffectGenerator[GeminiImageEditResult]:
        return (
            yield edit_image__gemini(
                prompt="Enhance the colors",
                model="gemini-2.5-flash-image-preview",
                images=[Image.open(BytesIO(uploaded))],
                temperature=0.5,
                top_k=8,
            )
        )

    engine = ProgramInterpreter()
    context = ExecutionContext(env={"gemini_client": client})
    result = await engine.run(flow(), context)

    assert result.is_ok
    payload = result.value
    assert payload.image_bytes == edited_bytes
    assert payload.mime_type == "image/png"
    assert payload.text == "Success"

    async_models.generate_content.assert_called_once()
    call_kwargs = async_models.generate_content.call_args.kwargs
    config = call_kwargs["config"]
    assert config.response_modalities == ["TEXT", "IMAGE"]

    api_calls = result.context.state.get("gemini_api_calls")
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
    def run_parallel() -> EffectGenerator[list[APICallMetadata]]:
        return (yield Gather(*(invoke(call) for call in call_defs)))

    engine = ProgramInterpreter()
    context = ExecutionContext()
    result = await engine.run(run_parallel(), context)

    assert result.is_ok
    state = result.context.state
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
