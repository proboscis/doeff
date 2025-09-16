"""Tests for the Gemini structured LLM implementation."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.genai import types as genai_types
from pydantic import BaseModel

from doeff import EffectGenerator, ExecutionContext, ProgramInterpreter, do

from doeff_gemini import (
    build_contents,
    build_generation_config,
    process_structured_response,
    process_unstructured_response,
    structured_llm__gemini,
)


class SimpleResponse(BaseModel):
    answer: str
    confidence: float


class ComplexResponse(BaseModel):
    title: str
    items: list[str]
    metadata: dict[str, Any]


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
async def test_process_structured_response_from_text() -> None:
    """Structured responses should parse JSON text when no parsed payload is present."""

    response = MagicMock()
    response.parsed = None
    response.text = json.dumps({"answer": "42", "confidence": 0.9})

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
