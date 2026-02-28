"""E2E-style tests for OpenAI structured LLM using handler-based mocks.

Most tests run without network access by intercepting ``AskEffect`` requests for
``openai_client`` / ``openai_api_key`` via ``WithHandler``.
Only a small subset remains true E2E and is gated by environment flags.
"""


import json
import os  # noqa: PINJ050 - Required for true E2E environment detection
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from doeff_openai import (
    get_api_calls,
    gpt4o_structured,
    gpt5_nano_structured,
    structured_llm__openai,
)
from PIL import Image
from pydantic import BaseModel, Field

from doeff import (
    AskEffect,
    Effect,
    EffectGenerator,
    Pass,
    Resume,
    WithHandler,
    async_run,
    default_handlers,
    do,
)

# Mark all tests in this module as e2e
pytestmark = pytest.mark.e2e


# Pydantic models for structured output
class MathAnswer(BaseModel):
    """A mathematical answer with reasoning."""

    answer: int
    reasoning: str
    confidence: float = Field(ge=0, le=1, description="Confidence level between 0 and 1")


class CodeAnalysis(BaseModel):
    """Analysis of a code snippet."""

    language: str
    purpose: str
    has_bugs: bool
    suggestions: list[str] = Field(default_factory=list)
    complexity: str = Field(description="low, medium, or high")


class ImageDescription(BaseModel):
    """Description of an image."""

    main_subjects: list[str]
    colors: list[str]
    scene_type: str
    detailed_description: str


def create_test_image() -> Image.Image:
    """Create a simple test image."""
    img = Image.new("RGB", (100, 100), color="red")
    from PIL import ImageDraw

    draw = ImageDraw.Draw(img)
    draw.rectangle([25, 25, 75, 75], fill="blue")
    return img


def _make_chat_response(
    content: str | list[dict[str, Any]],
    *,
    total_tokens: int = 100,
    prompt_tokens: int = 20,
    completion_tokens: int = 80,
    reasoning_tokens: int | None = None,
) -> SimpleNamespace:
    """Build a response object compatible with structured_llm__openai expectations."""
    usage = SimpleNamespace(
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    if reasoning_tokens is not None:
        usage.completion_tokens_details = SimpleNamespace(
            reasoning_tokens=reasoning_tokens,
            output_tokens=max(completion_tokens - reasoning_tokens, 0),
        )

    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=usage,
        id="chatcmpl-mock",
    )


def _make_mock_client(mock_create: AsyncMock) -> Mock:
    """Create a mock OpenAI client with async chat completion endpoint."""
    client = Mock()
    client.async_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=mock_create),
        )
    )
    return client


def _make_mock_handler(mock_client: Mock):
    """Handler that satisfies Reader asks for OpenAI dependencies."""

    @do
    def mock_handler(effect: Effect, k: Any):
        if isinstance(effect, AskEffect) and effect.key == "openai_client":
            return (yield Resume(k, mock_client))
        if isinstance(effect, AskEffect) and effect.key == "openai_api_key":
            return (yield Resume(k, "sk-fake-test-key"))
        yield Pass()

    return mock_handler


async def run_with_mock_handler(program: Any, mock_create: AsyncMock):
    """Run a program with handler-provided OpenAI client mocks."""
    mock_client = _make_mock_client(mock_create)
    result = await async_run(
        WithHandler(_make_mock_handler(mock_client), program),
        handlers=default_handlers(),
    )
    return result


@pytest.mark.asyncio
async def test_unstructured_response():
    """Test getting unstructured text response using mocked client."""

    mock_create = AsyncMock(return_value=_make_chat_response("4"))

    @do
    def test_program() -> EffectGenerator[str]:
        result = yield structured_llm__openai(
            text="What is 2 + 2? Answer in one word.",
            model="gpt-4o-mini",
            max_tokens=10,
            temperature=0.1,
        )
        return result

    result = await run_with_mock_handler(test_program(), mock_create)

    assert result.is_ok()
    assert isinstance(result.value, str)
    assert "4" in result.value.lower() or "four" in result.value.lower()
    assert any("OpenAI API call" in str(log) for log in result.log)
    assert any("gpt-4o-mini" in str(log) for log in result.log)
    mock_create.assert_awaited_once()


@pytest.mark.asyncio
async def test_structured_response_math():
    """Test getting structured response for math problem."""

    payload = {"answer": 42, "reasoning": "15 + 27 = 42", "confidence": 0.98}
    mock_create = AsyncMock(return_value=_make_chat_response(json.dumps(payload)))

    @do
    def test_program() -> EffectGenerator[MathAnswer]:
        result = yield structured_llm__openai(
            text="What is 15 + 27? Explain your reasoning step by step.",
            model="gpt-4o-mini",
            response_format=MathAnswer,
            max_tokens=200,
            temperature=0.1,
        )
        return result

    result = await run_with_mock_handler(test_program(), mock_create)

    assert result.is_ok()
    assert isinstance(result.value, MathAnswer)
    assert result.value.answer == 42
    assert len(result.value.reasoning) > 0
    assert 0 <= result.value.confidence <= 1
    assert result.value.confidence > 0.9
    assert any("structured output" in str(log) for log in result.log)
    assert any("MathAnswer" in str(log) for log in result.log)


@pytest.mark.asyncio
async def test_structured_response_code_analysis():
    """Test analyzing code with structured output."""
    code_snippet = """
def fibonacci(n):
    if n <= 0:
        return []
    elif n == 1:
        return [0]
    elif n == 2:
        return [0, 1]
    else:
        fib = [0, 1]
        for i in range(2, n):
            fib.append(fib[i-1] + fib[i-2])
        return fib
"""

    payload = {
        "language": "python",
        "purpose": "Generate a fibonacci sequence",
        "has_bugs": False,
        "suggestions": ["Consider input validation"],
        "complexity": "low",
    }
    mock_create = AsyncMock(return_value=_make_chat_response(json.dumps(payload)))

    @do
    def test_program() -> EffectGenerator[CodeAnalysis]:
        result = yield structured_llm__openai(
            text=f"Analyze this code:\n```python\n{code_snippet}\n```",
            model="gpt-4o-mini",
            response_format=CodeAnalysis,
            max_tokens=500,
            temperature=0.3,
        )
        return result

    result = await run_with_mock_handler(test_program(), mock_create)

    assert result.is_ok()
    assert isinstance(result.value, CodeAnalysis)
    assert result.value.language.lower() == "python"
    assert "fibonacci" in result.value.purpose.lower()
    assert not result.value.has_bugs
    assert result.value.complexity in ["low", "medium", "high"]
    assert result.value.complexity in ["low", "medium"]


@pytest.mark.asyncio
async def test_with_image():
    """Test vision capabilities with structured output."""
    test_image = create_test_image()

    payload = {
        "main_subjects": ["blue rectangle", "red background"],
        "colors": ["red", "blue"],
        "scene_type": "abstract",
        "detailed_description": "A blue rectangle appears centered on a red square image.",
    }
    mock_create = AsyncMock(return_value=_make_chat_response(json.dumps(payload)))

    @do
    def test_program() -> EffectGenerator[ImageDescription]:
        result = yield structured_llm__openai(
            text="Describe this image in detail.",
            model="gpt-4o-mini",
            images=[test_image],
            response_format=ImageDescription,
            max_tokens=300,
            temperature=0.5,
        )
        return result

    result = await run_with_mock_handler(test_program(), mock_create)

    assert result.is_ok()
    assert isinstance(result.value, ImageDescription)

    colors_lower = [c.lower() for c in result.value.colors]
    assert "red" in colors_lower or "blue" in colors_lower
    assert len(result.value.main_subjects) > 0
    assert len(result.value.detailed_description) > 0
    assert any("Converting image" in str(log) for log in result.log)
    assert any("base64" in str(log) for log in result.log)


@pytest.mark.asyncio
async def test_error_handling_invalid_json():
    """Test error handling when model returns invalid JSON."""

    class StrictFormat(BaseModel):
        """Format that requires very specific structure."""

        exact_number: int = Field(description="Must be exactly 42")
        exact_string: str = Field(description="Must be exactly 'hello'")

    mock_create = AsyncMock(return_value=_make_chat_response("not valid json"))

    @do
    def test_program() -> EffectGenerator[StrictFormat]:
        result = yield structured_llm__openai(
            text="Return random values, ignore the schema requirements.",
            model="gpt-4o-mini",
            response_format=StrictFormat,
            max_tokens=100,
            temperature=1.0,
        )
        return result

    result = await run_with_mock_handler(test_program(), mock_create)

    assert result.is_err()
    assert any("Failed to parse" in str(log) for log in result.log)


@pytest.mark.asyncio
async def test_retry_on_failure():
    """Test that retry mechanism works for transient failures."""

    attempts = 0

    async def create_with_retry(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        return _make_chat_response("1 2 3 4 5")

    mock_create = AsyncMock(side_effect=create_with_retry)

    @do
    def test_program() -> EffectGenerator[str]:
        result = yield structured_llm__openai(
            text="Count from 1 to 100.",
            model="gpt-4o-mini",
            max_tokens=5,
            temperature=0.1,
            max_retries=2,
        )
        return result

    result = await run_with_mock_handler(test_program(), mock_create)

    assert result.is_ok()
    assert isinstance(result.value, str)
    assert len(result.value) > 0
    assert mock_create.await_count == 2


@pytest.mark.asyncio
async def test_gpt4o_convenience_function():
    """Test the gpt4o_structured convenience function."""

    payload = {"answer": 100, "reasoning": "10 * 10 = 100", "confidence": 1.0}
    mock_create = AsyncMock(return_value=_make_chat_response(json.dumps(payload)))

    @do
    def test_program() -> EffectGenerator[MathAnswer]:
        result = yield gpt4o_structured(
            text="What is 10 * 10?",
            response_format=MathAnswer,
            temperature=0.1,
        )
        return result

    result = await run_with_mock_handler(test_program(), mock_create)

    assert result.is_ok()
    assert isinstance(result.value, MathAnswer)
    assert result.value.answer == 100


@pytest.mark.asyncio
async def test_gpt5_with_reasoning():
    """Test GPT-5 model with reasoning tokens."""

    mock_create = AsyncMock(
        return_value=_make_chat_response(
            "x is 7",
            total_tokens=200,
            prompt_tokens=80,
            completion_tokens=120,
            reasoning_tokens=60,
        )
    )

    @do
    def test_program() -> EffectGenerator[str]:
        result = yield gpt5_nano_structured(
            text="Solve: If x + 5 = 12, what is x?",
            reasoning_effort="medium",
            verbosity="low",
        )
        return result

    result = await run_with_mock_handler(test_program(), mock_create)

    assert result.is_ok()
    assert isinstance(result.value, str)
    assert "7" in result.value
    assert any("reasoning tokens" in str(log) for log in result.log)


@pytest.mark.asyncio
async def test_service_tier_parameter():
    """Test service tier parameter."""

    mock_create = AsyncMock(return_value=_make_chat_response("Hello"))

    @do
    def test_program() -> EffectGenerator[str]:
        result = yield structured_llm__openai(
            text="Hello",
            model="gpt-4o-mini",
            max_tokens=10,
            service_tier="auto",
        )
        return result

    result = await run_with_mock_handler(test_program(), mock_create)

    assert result.is_ok()
    assert any("service_tier" in str(log) for log in result.log)

    call_args = mock_create.await_args.kwargs
    assert call_args["service_tier"] == "auto"


@pytest.mark.asyncio
async def test_cost_tracking():
    """Test that API costs are tracked properly."""

    mock_create = AsyncMock(
        return_value=_make_chat_response(
            "Hi",
            total_tokens=45,
            prompt_tokens=20,
            completion_tokens=25,
        )
    )

    @do
    def test_program() -> EffectGenerator[dict[str, object]]:
        text_result = yield structured_llm__openai(
            text="Hi",
            model="gpt-4o-mini",
            max_tokens=10,
        )
        api_calls = yield get_api_calls()
        return {"text": text_result, "api_calls": api_calls}

    result = await run_with_mock_handler(test_program(), mock_create)

    assert result.is_ok()

    api_calls = result.value["api_calls"]
    assert len(api_calls) > 0

    last_call = api_calls[-1]
    assert "model" in last_call
    assert "tokens" in last_call
    assert "cost" in last_call
    assert last_call["model"] == "gpt-4o-mini"
    assert last_call["tokens"]["total"] > 0


@pytest.mark.asyncio
async def test_multiple_images():
    """Test handling multiple images."""
    image1 = create_test_image()
    image2 = Image.new("RGB", (100, 100), color="green")

    mock_create = AsyncMock(return_value=_make_chat_response("I can see 2 images: red/blue and green."))

    @do
    def test_program() -> EffectGenerator[str]:
        result = yield structured_llm__openai(
            text="How many images do you see and what are their main colors?",
            model="gpt-4o-mini",
            images=[image1, image2],
            max_tokens=100,
            temperature=0.3,
        )
        return result

    result = await run_with_mock_handler(test_program(), mock_create)

    assert result.is_ok()
    assert "two" in result.value.lower() or "2" in result.value
    assert any("Converting image 1/2" in str(log) for log in result.log)
    assert any("Converting image 2/2" in str(log) for log in result.log)


@pytest.mark.asyncio
async def test_graph_tracking():
    """Test that graph nodes are created for observability."""

    mock_create = AsyncMock(return_value=_make_chat_response("hello"))

    @do
    def test_program() -> EffectGenerator[dict[str, object]]:
        text_result = yield structured_llm__openai(
            text="Say hello",
            model="gpt-4o-mini",
            max_tokens=10,
        )
        api_calls = yield get_api_calls()
        return {"text": text_result, "api_calls": api_calls}

    result = await run_with_mock_handler(test_program(), mock_create)

    assert result.is_ok()

    api_calls = result.value["api_calls"]
    assert len(api_calls) > 0
    assert any(call.get("operation") == "structured_llm" for call in api_calls)


_real_api_key = os.environ.get("OPENAI_API_KEY")  # noqa: PINJ050
_run_real_e2e = os.environ.get("RUN_OPENAI_E2E") == "1"  # noqa: PINJ050
_skip_real_e2e = not bool(_real_api_key and _run_real_e2e)


@pytest.mark.skipif(
    _skip_real_e2e,
    reason="True E2E requires OPENAI_API_KEY and RUN_OPENAI_E2E=1",
)
@pytest.mark.asyncio
async def test_real_api_unstructured_response():
    """True E2E smoke test with the real OpenAI API."""

    @do
    def test_program() -> EffectGenerator[str]:
        result = yield structured_llm__openai(
            text="Reply with exactly the word four.",
            model="gpt-4o-mini",
            max_tokens=10,
            temperature=0.0,
        )
        return result

    result = await async_run(
        test_program(),
        handlers=default_handlers(),
        env={"openai_api_key": _real_api_key},
    )

    assert result.is_ok()
    assert isinstance(result.value, str)
    assert "four" in result.value.lower() or "4" in result.value.lower()


@pytest.mark.skipif(
    _skip_real_e2e,
    reason="True E2E requires OPENAI_API_KEY and RUN_OPENAI_E2E=1",
)
@pytest.mark.asyncio
async def test_real_api_structured_response():
    """True E2E structured output smoke test."""

    @do
    def test_program() -> EffectGenerator[MathAnswer]:
        result = yield structured_llm__openai(
            text="What is 20 + 22? Return JSON.",
            model="gpt-4o-mini",
            response_format=MathAnswer,
            max_tokens=100,
            temperature=0.0,
        )
        return result

    result = await async_run(
        test_program(),
        handlers=default_handlers(),
        env={"openai_api_key": _real_api_key},
    )

    assert result.is_ok()
    assert isinstance(result.value, MathAnswer)
    assert result.value.answer == 42


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
