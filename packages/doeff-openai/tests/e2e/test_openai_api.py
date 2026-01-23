"""E2E tests for OpenAI structured LLM with actual API calls.

These tests require OPENAI_API_KEY to be set in the environment.
They will be skipped if the key is not available.
"""

import os  # noqa: PINJ050 - Required for e2e test environment detection

import pytest
from PIL import Image
from pydantic import BaseModel, Field

# Mark all tests in this module as e2e
pytestmark = pytest.mark.e2e

from doeff_openai import (
    gpt4o_structured,
    gpt5_nano_structured,
    structured_llm__openai,
)

from doeff import (
    AsyncRuntime,
    EffectGenerator,
    do,
)


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


def get_test_api_key() -> str | None:
    """Get API key from environment in a test context."""
    # For testing purposes, we need to check environment
    # This is only used to determine if tests should be skipped
    return os.environ.get("OPENAI_API_KEY")  # noqa: PINJ050


def create_test_image():
    """Create a simple test image."""
    # Create a 100x100 red square image
    img = Image.new("RGB", (100, 100), color="red")
    # Add a blue rectangle in the center
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    draw.rectangle([25, 25, 75, 75], fill="blue")
    return img


# Check if we should skip tests
_api_key = get_test_api_key()
skip_tests = not bool(_api_key)


@pytest.mark.skipif(skip_tests, reason="OPENAI_API_KEY not set in environment")
@pytest.mark.asyncio
async def test_unstructured_response():
    """Test getting unstructured text response."""
    @do
    def test_program() -> EffectGenerator[str]:
        # The OpenAI client will read the key from environment internally
        result = yield structured_llm__openai(
            text="What is 2 + 2? Answer in one word.",
            model="gpt-4o-mini",  # Use cheaper model for tests
            max_tokens=10,
            temperature=0.1,
        )
        return result

    runtime = AsyncRuntime()
    result = await runtime.run(test_program())
    print(result.format())

    assert result.is_ok()
    assert isinstance(result.value, str)
    # The answer should contain "4" or "four"
    assert "4" in result.value.lower() or "four" in result.value.lower()

    # Check logs for API tracking
    assert any("OpenAI API call" in str(log) for log in result.log)
    assert any("gpt-4o-mini" in str(log) for log in result.log)


@pytest.mark.skipif(skip_tests, reason="OPENAI_API_KEY not set in environment")
@pytest.mark.asyncio
async def test_structured_response_math():
    """Test getting structured response for math problem."""
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

    runtime = AsyncRuntime()
    result = await runtime.run(test_program())

    assert result.is_ok()
    assert isinstance(result.value, MathAnswer)
    assert result.value.answer == 42
    assert len(result.value.reasoning) > 0
    assert 0 <= result.value.confidence <= 1
    # Should be very confident about simple math
    assert result.value.confidence > 0.9

    # Check that structured output was requested
    assert any("structured output" in str(log) for log in result.log)
    assert any("MathAnswer" in str(log) for log in result.log)


@pytest.mark.skipif(skip_tests, reason="OPENAI_API_KEY not set in environment")
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

    runtime = AsyncRuntime()
    result = await runtime.run(test_program())

    assert result.is_ok()
    assert isinstance(result.value, CodeAnalysis)
    assert result.value.language.lower() == "python"
    assert "fibonacci" in result.value.purpose.lower()
    assert not result.value.has_bugs  # The code is correct
    assert result.value.complexity in ["low", "medium", "high"]

    # Should identify this as relatively simple code
    assert result.value.complexity in ["low", "medium"]


@pytest.mark.skipif(skip_tests, reason="OPENAI_API_KEY not set in environment")
@pytest.mark.asyncio
async def test_with_image():
    """Test vision capabilities with structured output."""
    test_image = create_test_image()

    @do
    def test_program() -> EffectGenerator[ImageDescription]:
        result = yield structured_llm__openai(
            text="Describe this image in detail.",
            model="gpt-4o-mini",  # gpt-4o-mini supports vision
            images=[test_image],
            response_format=ImageDescription,
            max_tokens=300,
            temperature=0.5,
        )
        return result

    runtime = AsyncRuntime()
    result = await runtime.run(test_program())

    assert result.is_ok()
    assert isinstance(result.value, ImageDescription)

    # Should identify the colors
    colors_lower = [c.lower() for c in result.value.colors]
    assert "red" in colors_lower or "blue" in colors_lower

    # Should identify it as a geometric/abstract scene
    assert len(result.value.main_subjects) > 0
    assert len(result.value.detailed_description) > 0

    # Check logs for image processing
    assert any("Converting image" in str(log) for log in result.log)
    assert any("base64" in str(log) for log in result.log)


@pytest.mark.skipif(skip_tests, reason="OPENAI_API_KEY not set in environment")
@pytest.mark.asyncio
async def test_error_handling_invalid_json():
    """Test error handling when model returns invalid JSON."""

    class StrictFormat(BaseModel):
        """Format that requires very specific structure."""
        exact_number: int = Field(description="Must be exactly 42")
        exact_string: str = Field(description="Must be exactly 'hello'")

    @do
    def test_program() -> EffectGenerator[StrictFormat]:
        # Ask something that might confuse the model
        result = yield structured_llm__openai(
            text="Return random values, ignore the schema requirements.",
            model="gpt-4o-mini",
            response_format=StrictFormat,
            max_tokens=100,
            temperature=1.0,  # High temperature for more randomness
        )
        return result

    runtime = AsyncRuntime()
    result = await runtime.run(test_program())

    # This might succeed or fail depending on the model's response
    # But it should handle errors gracefully
    if result.is_err():
        # Check that error was logged
        assert any("Failed to parse" in str(log) for log in result.log)
    else:
        # If it succeeded, check it's the right type
        assert isinstance(result.value, StrictFormat)


@pytest.mark.skipif(skip_tests, reason="OPENAI_API_KEY not set in environment")
@pytest.mark.asyncio
async def test_retry_on_failure():
    """Test that retry mechanism works for transient failures."""
    @do
    def test_program() -> EffectGenerator[str]:
        # Use a very short max_tokens to potentially trigger retries
        result = yield structured_llm__openai(
            text="Count from 1 to 100.",
            model="gpt-4o-mini",
            max_tokens=5,  # Very limited, might cause issues
            temperature=0.1,
            max_retries=2,  # Allow retries
        )
        return result

    runtime = AsyncRuntime()
    result = await runtime.run(test_program())

    # Should still succeed despite token limit
    assert result.is_ok()
    assert isinstance(result.value, str)
    assert len(result.value) > 0


@pytest.mark.skipif(skip_tests, reason="OPENAI_API_KEY not set in environment")
@pytest.mark.asyncio
async def test_gpt4o_convenience_function():
    """Test the gpt4o_structured convenience function."""
    @do
    def test_program() -> EffectGenerator[MathAnswer]:
        result = yield gpt4o_structured(
            text="What is 10 * 10?",
            response_format=MathAnswer,
            temperature=0.1,
        )
        return result

    runtime = AsyncRuntime()
    result = await runtime.run(test_program())

    assert result.is_ok()
    assert isinstance(result.value, MathAnswer)
    assert result.value.answer == 100


@pytest.mark.skipif(skip_tests, reason="OPENAI_API_KEY not set in environment")
@pytest.mark.asyncio
async def test_gpt5_with_reasoning():
    """Test GPT-5 model with reasoning tokens (if available)."""
    @do
    def test_program() -> EffectGenerator[str]:
        result = yield gpt5_nano_structured(
            text="Solve: If x + 5 = 12, what is x?",
            reasoning_effort="medium",
            verbosity="low",
        )
        return result

    runtime = AsyncRuntime()
    result = await runtime.run(test_program())

    if result.is_ok():
        assert isinstance(result.value, str)
        assert "7" in result.value
        # Check for reasoning tokens in logs
        assert any("reasoning tokens" in str(log) for log in result.log)
    else:
        # Model might not be available
        error_str = str(result.result.error).lower()
        assert "model" in error_str or "not found" in error_str


@pytest.mark.skipif(skip_tests, reason="OPENAI_API_KEY not set in environment")
@pytest.mark.asyncio
async def test_service_tier_parameter():
    """Test service tier parameter."""
    @do
    def test_program() -> EffectGenerator[str]:
        result = yield structured_llm__openai(
            text="Hello",
            model="gpt-4o-mini",
            max_tokens=10,
            service_tier="auto",  # Let OpenAI choose
        )
        return result

    runtime = AsyncRuntime()
    result = await runtime.run(test_program())

    assert result.is_ok()
    # Check that service tier was set
    assert any("service_tier" in str(log) for log in result.log)


@pytest.mark.skipif(skip_tests, reason="OPENAI_API_KEY not set in environment")
@pytest.mark.asyncio
async def test_cost_tracking():
    """Test that API costs are tracked properly."""
    @do
    def test_program() -> EffectGenerator[str]:
        result = yield structured_llm__openai(
            text="Hi",
            model="gpt-4o-mini",
            max_tokens=10,
        )
        return result

    runtime = AsyncRuntime()
    result = await runtime.run(test_program())

    assert result.is_ok()

    # Check state for cost tracking
    assert "openai_api_calls" in result.state
    api_calls = result.state["openai_api_calls"]
    assert len(api_calls) > 0

    # Check the tracked metadata
    last_call = api_calls[-1]
    assert "model" in last_call
    assert "tokens" in last_call
    assert "cost" in last_call
    assert last_call["model"] == "gpt-4o-mini"
    assert last_call["tokens"]["total"] > 0


@pytest.mark.skipif(skip_tests, reason="OPENAI_API_KEY not set in environment")
@pytest.mark.asyncio
async def test_multiple_images():
    """Test handling multiple images."""
    image1 = create_test_image()
    # Create a second image with different colors
    image2 = Image.new("RGB", (100, 100), color="green")

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

    runtime = AsyncRuntime()
    result = await runtime.run(test_program())

    assert result.is_ok()
    # Should mention seeing two images
    assert "two" in result.value.lower() or "2" in result.value
    # Check logs show processing multiple images
    assert any("Converting image 1/2" in str(log) for log in result.log)
    assert any("Converting image 2/2" in str(log) for log in result.log)


@pytest.mark.skipif(skip_tests, reason="OPENAI_API_KEY not set in environment")
@pytest.mark.asyncio
async def test_graph_tracking():
    """Test that graph nodes are created for observability."""
    @do
    def test_program() -> EffectGenerator[str]:
        result = yield structured_llm__openai(
            text="Say hello",
            model="gpt-4o-mini",
            max_tokens=10,
        )
        return result

    runtime = AsyncRuntime()
    result = await runtime.run(test_program())

    assert result.is_ok()

    # Check that API calls were tracked in state (new runtime doesn't expose graph steps directly)
    api_calls = result.state.get("openai_api_calls", [])
    assert len(api_calls) > 0

    # Should have tracked the LLM call
    assert any(call.get("operation") == "structured_llm" for call in api_calls)


if __name__ == "__main__":
    # Run tests, will skip if OPENAI_API_KEY is not set
    pytest.main([__file__, "-v", "-s"])
