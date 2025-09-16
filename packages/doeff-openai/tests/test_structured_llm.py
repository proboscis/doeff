"""Tests for structured LLM implementation with doeff effects."""

import json
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest
from doeff_openai import (
    gpt5_structured,
    is_gpt5_model,
    requires_max_completion_tokens,
    structured_llm__openai,
)
from doeff_openai.structured_llm import (
    build_api_parameters,
    build_messages,
    ensure_strict_schema,
    process_structured_response,
    process_unstructured_response,
)
from pydantic import BaseModel

from doeff import (
    Ask,
    EffectGenerator,
    ExecutionContext,
    ProgramInterpreter,
    do,
)


# Test models for structured output
class SimpleResponse(BaseModel):
    answer: str
    confidence: float


class ComplexResponse(BaseModel):
    title: str
    items: list[str]
    metadata: dict[str, Any]
    nested: SimpleResponse | None = None


# Model detection tests
def test_is_gpt5_model():
    """Test GPT-5 model detection."""
    assert is_gpt5_model("gpt-5")
    assert is_gpt5_model("gpt-5-nano")
    assert is_gpt5_model("GPT-5")
    assert is_gpt5_model("gpt5-turbo")
    assert not is_gpt5_model("gpt-4o")
    assert not is_gpt5_model("gpt-3.5-turbo")


def test_requires_max_completion_tokens():
    """Test models requiring max_completion_tokens."""
    assert requires_max_completion_tokens("gpt-5")
    assert requires_max_completion_tokens("gpt-5-nano")
    assert requires_max_completion_tokens("o1-preview")
    assert requires_max_completion_tokens("o3-mini")
    assert requires_max_completion_tokens("o4")
    assert not requires_max_completion_tokens("gpt-4o")
    assert not requires_max_completion_tokens("gpt-3.5-turbo")


# Schema handling tests
def test_ensure_strict_schema_simple():
    """Test ensuring strict schema for simple object."""
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "number"}
        }
    }

    result = ensure_strict_schema(schema)
    assert result["additionalProperties"] is False
    assert result["properties"] == schema["properties"]


def test_ensure_strict_schema_nested():
    """Test ensuring strict schema for nested objects."""
    schema = {
        "type": "object",
        "properties": {
            "user": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"}
                }
            }
        }
    }

    result = ensure_strict_schema(schema)
    assert result["additionalProperties"] is False
    assert result["properties"]["user"]["additionalProperties"] is False


def test_ensure_strict_schema_array():
    """Test ensuring strict schema for arrays."""
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"}
            }
        }
    }

    result = ensure_strict_schema(schema)
    assert result["items"]["additionalProperties"] is False


# Message building tests
@pytest.mark.asyncio
async def test_build_messages_text_only():
    """Test building messages with text only."""

    @do
    def test_flow() -> EffectGenerator[list]:
        messages = yield build_messages("Hello, world!")
        return messages

    engine = ProgramInterpreter()
    result = await engine.run(test_flow())

    assert result.is_ok
    assert len(result.value) == 1
    assert result.value[0]["role"] == "user"
    assert result.value[0]["content"] == "Hello, world!"


@pytest.mark.skip(reason="PIL image handling requires actual image data")
@pytest.mark.asyncio
async def test_build_messages_with_images():
    """Test building messages with images."""
    # This would require mocking PIL images


# API parameter building tests
@pytest.mark.asyncio
async def test_build_api_parameters_gpt4():
    """Test building parameters for GPT-4."""

    @do
    def test_flow() -> EffectGenerator[dict]:
        messages = [{"role": "user", "content": "test"}]
        params = yield build_api_parameters(
            model="gpt-4o",
            messages=messages,
            temperature=0.5,
            max_tokens=1000,
            reasoning_effort=None,
            verbosity=None,
            service_tier=None,
            response_format=None,
        )
        return params

    engine = ProgramInterpreter()
    result = await engine.run(test_flow())
    print(result.display())

    assert result.is_ok
    assert result.value["model"] == "gpt-4o"
    assert result.value["messages"] == [{"role": "user", "content": "test"}]
    assert result.value["temperature"] == 0.5
    assert result.value["max_tokens"] == 1000
    assert "max_completion_tokens" not in result.value


@pytest.mark.asyncio
async def test_build_api_parameters_gpt5():
    """Test building parameters for GPT-5."""

    @do
    def test_flow() -> EffectGenerator[dict]:
        messages = [{"role": "user", "content": "test"}]
        params = yield build_api_parameters(
            model="gpt-5",
            messages=messages,
            temperature=0.5,  # Should be ignored
            max_tokens=1000,
            reasoning_effort="high",
            verbosity="medium",
            service_tier="priority",
            response_format=None,
        )
        return params

    engine = ProgramInterpreter()
    result = await engine.run(test_flow())

    assert result.is_ok
    assert result.value["model"] == "gpt-5"
    assert result.value["max_completion_tokens"] == 1000
    assert "max_tokens" not in result.value
    assert "temperature" not in result.value  # GPT-5 doesn't support custom temperature
    assert result.value["reasoning_effort"] == "high"
    assert result.value["verbosity"] == "medium"
    assert result.value["service_tier"] == "priority"


@pytest.mark.asyncio
async def test_build_api_parameters_structured():
    """Test building parameters with structured output."""

    @do
    def test_flow() -> EffectGenerator[dict]:
        messages = [{"role": "user", "content": "test"}]
        params = yield build_api_parameters(
            model="gpt-4o",
            messages=messages,
            temperature=0.7,
            max_tokens=1000,
            reasoning_effort=None,
            verbosity=None,
            service_tier=None,
            response_format=SimpleResponse,
        )
        return params

    engine = ProgramInterpreter()
    result = await engine.run(test_flow())
    print(result.display())

    assert result.is_ok
    assert "response_format" in result.value
    assert result.value["response_format"]["type"] == "json_schema"
    assert result.value["response_format"]["json_schema"]["name"] == "SimpleResponse"
    assert result.value["response_format"]["json_schema"]["strict"] is True


# Response processing tests
@pytest.mark.asyncio
async def test_process_structured_response_success():
    """Test processing successful structured response."""

    # Mock response
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "answer": "42",
        "confidence": 0.95
    })

    @do
    def test_flow() -> EffectGenerator[SimpleResponse]:
        result = yield process_structured_response(mock_response, SimpleResponse)
        return result

    engine = ProgramInterpreter()
    result = await engine.run(test_flow())

    assert result.is_ok
    assert isinstance(result.value, SimpleResponse)
    assert result.value.answer == "42"
    assert result.value.confidence == 0.95


@pytest.mark.asyncio
async def test_process_structured_response_invalid_json():
    """Test processing structured response with invalid JSON."""

    # Mock response with invalid JSON
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "not valid json"

    @do
    def test_flow() -> EffectGenerator[Any]:
        result = yield process_structured_response(mock_response, SimpleResponse)
        return result

    engine = ProgramInterpreter()
    result = await engine.run(test_flow())
    print(result.display())

    assert result.is_err
    assert "Expecting value" in str(result.result.error)


@pytest.mark.asyncio
async def test_process_unstructured_response():
    """Test processing unstructured text response."""

    # Mock response
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "This is a test response."

    @do
    def test_flow() -> EffectGenerator[str]:
        result = yield process_unstructured_response(mock_response)
        return result

    engine = ProgramInterpreter()
    result = await engine.run(test_flow())

    assert result.is_ok
    assert result.value == "This is a test response."


# Main structured LLM tests
@pytest.mark.asyncio
async def test_structured_llm_text_only():
    """Test structured LLM with text-only input."""

    # Mock OpenAI client and response
    mock_client = Mock()
    mock_sync_client = MagicMock()
    mock_client.sync_client = mock_sync_client

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Test response"
    mock_response.usage = MagicMock()
    mock_response.usage.total_tokens = 100
    mock_response.usage.prompt_tokens = 20
    mock_response.usage.completion_tokens = 80

    mock_sync_client.chat.completions.create.return_value = mock_response

    @do
    def test_flow() -> EffectGenerator[str]:
        # Provide mock client in environment
        yield Ask("openai_client")  # This will be provided by context

        result = yield structured_llm__openai(
            text="What is 2+2?",
            model="gpt-4o",
            max_tokens=100,
        )
        return result

    engine = ProgramInterpreter()
    context = ExecutionContext(env={"openai_client": mock_client})
    result = await engine.run(test_flow(), context)

    assert result.is_ok
    assert result.value == "Test response"

    # Check API was called
    mock_sync_client.chat.completions.create.assert_called_once()
    call_args = mock_sync_client.chat.completions.create.call_args[1]
    assert call_args["model"] == "gpt-4o"
    assert call_args["max_tokens"] == 100


@pytest.mark.asyncio
async def test_structured_llm_with_pydantic():
    """Test structured LLM with Pydantic model output."""

    # Mock OpenAI client and response
    mock_client = Mock()
    mock_sync_client = MagicMock()
    mock_client.sync_client = mock_sync_client

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "answer": "4",
        "confidence": 1.0
    })
    mock_response.usage = MagicMock()
    mock_response.usage.total_tokens = 150
    mock_response.usage.prompt_tokens = 50
    mock_response.usage.completion_tokens = 100

    mock_sync_client.chat.completions.create.return_value = mock_response

    @do
    def test_flow() -> EffectGenerator[SimpleResponse]:
        # Provide mock client in environment
        yield Ask("openai_client")  # This will be provided by context

        result = yield structured_llm__openai(
            text="What is 2+2?",
            model="gpt-4o",
            response_format=SimpleResponse,
            max_tokens=100,
        )
        return result

    engine = ProgramInterpreter()
    context = ExecutionContext(env={"openai_client": mock_client})
    result = await engine.run(test_flow(), context)

    assert result.is_ok
    assert isinstance(result.value, SimpleResponse)
    assert result.value.answer == "4"
    assert result.value.confidence == 1.0

    # Check API was called with structured output
    mock_sync_client.chat.completions.create.assert_called_once()
    call_args = mock_sync_client.chat.completions.create.call_args[1]
    assert "response_format" in call_args
    assert call_args["response_format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_gpt5_structured_convenience():
    """Test GPT-5 convenience function."""

    # Mock OpenAI client and response
    mock_client = Mock()
    mock_sync_client = MagicMock()
    mock_client.sync_client = mock_sync_client

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "GPT-5 response"
    mock_response.usage = MagicMock()
    mock_response.usage.total_tokens = 200
    mock_response.usage.prompt_tokens = 50
    mock_response.usage.completion_tokens = 150

    # Mock GPT-5 specific details
    mock_response.usage.completion_tokens_details = MagicMock()
    mock_response.usage.completion_tokens_details.reasoning_tokens = 100
    mock_response.usage.completion_tokens_details.output_tokens = 50

    mock_sync_client.chat.completions.create.return_value = mock_response

    @do
    def test_flow() -> EffectGenerator[str]:
        # Provide mock client in environment
        yield Ask("openai_client")  # This will be provided by context

        result = yield gpt5_structured(
            text="Solve this complex problem",
            reasoning_effort="high",
        )
        return result

    engine = ProgramInterpreter()
    context = ExecutionContext(env={"openai_client": mock_client})
    result = await engine.run(test_flow(), context)

    assert result.is_ok
    assert result.value == "GPT-5 response"

    # Check API was called with GPT-5 parameters
    mock_sync_client.chat.completions.create.assert_called_once()
    call_args = mock_sync_client.chat.completions.create.call_args[1]
    assert call_args["model"] == "gpt-5"
    assert call_args["reasoning_effort"] == "high"
    assert "max_completion_tokens" in call_args
    assert "max_tokens" not in call_args

    # Check logs mention reasoning tokens
    assert any("reasoning tokens" in str(log) for log in result.log)


# Integration test (requires actual API key)
@pytest.mark.skip(reason="Requires actual OpenAI API key")
@pytest.mark.asyncio
async def test_structured_llm_integration():
    """Integration test with actual OpenAI API."""

    @do
    def test_flow() -> EffectGenerator[SimpleResponse]:
        result = yield structured_llm__openai(
            text="What is 2+2? Respond with JSON containing 'answer' (string) and 'confidence' (float 0-1).",
            model="gpt-4o",
            response_format=SimpleResponse,
            max_tokens=100,
            temperature=0.1,
        )
        return result

    from doeff import run_with_env

    # Get API key from environment in a compliant way
    @do
    def get_api_key() -> EffectGenerator[str]:
        api_key = yield Ask("openai_api_key")
        return api_key

    # First check if API key is available
    engine = ProgramInterpreter()
    key_result = await engine.run(get_api_key(), ExecutionContext(env={}))

    if not key_result.is_ok or not key_result.value:
        pytest.skip("OPENAI_API_KEY not set")

    result = await run_with_env(
        test_flow(),
        env={"openai_api_key": key_result.value}
    )

    assert result.is_ok
    assert isinstance(result.value, SimpleResponse)
    assert "4" in result.value.answer.lower()
    assert result.value.confidence > 0.9


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
