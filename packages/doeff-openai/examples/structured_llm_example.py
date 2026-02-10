#!/usr/bin/env python3
"""Example demonstrating structured LLM usage with doeff-openai.

This example shows how to use the structured LLM implementation with:
1. Plain text responses
2. Structured output using Pydantic models
3. GPT-5 thinking mode features
4. Error handling with effects
5. Cost tracking

To run this example with pinjected:
    python -m pinjected run doeff_openai.examples.structured_llm_example.a_run_all_examples \
        --openai-api-key "sk-..."
"""

from typing import Protocol

from doeff_openai import (
    get_total_cost,
    gpt5_nano_structured,
    reset_cost_tracking,
    structured_llm__openai,
)
from pinjected import injected, instance
from pydantic import BaseModel, Field

from doeff import (
    Ask,
    EffectGenerator,
    Safe,
    do,
    run,
    slog,
)


# Define structured output models
class CityInfo(BaseModel):
    """Information about a city."""
    name: str = Field(description="The city name")
    country: str = Field(description="The country where the city is located")
    population: int = Field(description="The population of the city")
    is_capital: bool = Field(description="Whether this city is a capital")
    famous_landmarks: list[str] = Field(description="List of famous landmarks")


class MathProblem(BaseModel):
    """Solution to a math problem."""
    problem: str = Field(description="The original problem statement")
    steps: list[str] = Field(description="Step-by-step solution")
    answer: float = Field(description="The final numerical answer")
    confidence: float = Field(description="Confidence in the answer (0-1)")


class CodeAnalysis(BaseModel):
    """Analysis of code snippet."""
    language: str = Field(description="Programming language")
    complexity: str = Field(description="Complexity level: simple, moderate, complex")
    issues: list[str] = Field(description="List of potential issues")
    improvements: list[str] = Field(description="Suggested improvements")
    has_security_issues: bool = Field(description="Whether there are security concerns")


# Example 1: Simple text response
@do
def example_plain_text() -> EffectGenerator[str]:
    """Example of getting a plain text response."""
    yield slog(step="example1", msg="Plain text response")

    result = yield structured_llm__openai(
        text="What is the capital of France? Answer in one sentence.",
        model="gpt-4o",
        max_tokens=100,
        temperature=0.5,
    )

    yield slog(step="example1", response=result)
    return result


# Example 2: Structured output with Pydantic
@do
def example_structured_output() -> EffectGenerator[CityInfo]:
    """Example of getting structured output."""
    yield slog(step="example2", msg="Structured output with Pydantic")

    result = yield structured_llm__openai(
        text="Tell me about Tokyo, Japan. Include population and famous landmarks.",
        model="gpt-4o",
        response_format=CityInfo,
        max_tokens=500,
        temperature=0.3,
    )

    yield slog(
        step="example2",
        city=result.name,
        population=result.population,
        is_capital=result.is_capital,
        landmarks=result.famous_landmarks,
    )

    return result


# Example 3: Math problem with optional GPT-5
@do
def example_gpt5_reasoning() -> EffectGenerator[MathProblem]:
    """Example of using GPT-5 with reasoning mode for complex problem solving."""
    yield slog(step="example3", msg="GPT-5 with thinking mode (if available)")

    # Check if GPT-5 is available via Ask effect
    gpt5_available = yield Ask("gpt5_available")
    model_name = "gpt-5-nano" if gpt5_available else "gpt-4o"

    yield slog(step="example3", model=model_name)

    problem_text = """Solve this problem step by step:
    
    A train leaves Station A at 10:00 AM traveling at 60 mph.
    Another train leaves Station B at 10:30 AM traveling at 80 mph toward Station A.
    The stations are 280 miles apart.
    At what time will the trains meet?
    """

    if gpt5_available:
        result = yield gpt5_nano_structured(
            text=problem_text,
            response_format=MathProblem,
            reasoning_effort="high",  # Use high reasoning for complex problems
            max_tokens=1000,
        )
    else:
        result = yield structured_llm__openai(
            text=problem_text,
            model=model_name,
            response_format=MathProblem,
            max_tokens=1000,
            temperature=0.2,
        )

    yield slog(
        step="example3",
        problem=result.problem,
        solution_steps=result.steps,
        answer=result.answer,
        confidence=f"{result.confidence:.1%}",
    )

    return result


# Example 4: Code analysis with error handling
@do
def example_code_analysis() -> EffectGenerator[CodeAnalysis | None]:
    """Example of analyzing code with error handling."""
    yield slog(step="example4", msg="Code analysis with error handling")

    code_snippet = """
    def calculate_average(numbers):
        total = 0
        for num in numbers:
            total += num
        return total / len(numbers)
    """

    # Using Safe effect for error handling
    @do
    def analyze_code():
        result = yield structured_llm__openai(
            text=f"Analyze this Python code:\n```python\n{code_snippet}\n```",
            model="gpt-4o",
            response_format=CodeAnalysis,
            max_tokens=500,
            temperature=0.1,
        )
        return result

    safe_result = yield Safe(analyze_code())

    if safe_result.is_err():
        yield slog(step="example4", status="error", error=str(safe_result.error))
        result = None
    else:
        result = safe_result.value

    if result:
        yield slog(
            step="example4",
            language=result.language,
            complexity=result.complexity,
            issues=result.issues,
            improvements=result.improvements,
            has_security_issues=result.has_security_issues,
        )

    return result


# Example 5: Cost tracking
@do
def example_with_cost_tracking() -> EffectGenerator[None]:
    """Example showing cost tracking across multiple API calls."""
    yield slog(step="example5", msg="Cost tracking")

    # Reset cost tracking for this example
    yield reset_cost_tracking()

    # Make several API calls
    yield slog(step="example5", status="making_calls")

    # Call 1: Simple question
    yield structured_llm__openai(
        text="What is 2+2?",
        model="gpt-4o",
        max_tokens=50,
    )

    # Call 2: Structured output
    yield structured_llm__openai(
        text="List 3 programming languages",
        model="gpt-4o",
        max_tokens=100,
    )

    # Call 3: Another structured output
    yield structured_llm__openai(
        text="Describe Python in one sentence",
        model="gpt-4o",
        response_format=None,  # Plain text
        max_tokens=100,
    )

    # Get total cost
    total_cost = yield get_total_cost()
    yield slog(step="example5", total_cost=f"${total_cost:.6f}")

    return None


# Main program that runs all examples
@do
def _run_all_examples_impl() -> EffectGenerator[None]:
    """Run all examples in sequence."""
    yield slog(step="main", msg="DoEff OpenAI Structured LLM Examples")

    # Check if API key is available
    api_key = yield Ask("openai_api_key")
    if not api_key:
        yield slog(step="main", status="error", msg="OPENAI_API_KEY not provided")
        return None

    # Run examples
    try:
        yield example_plain_text()
        yield example_structured_output()
        yield example_gpt5_reasoning()
        yield example_code_analysis()
        yield example_with_cost_tracking()

    except Exception as e:
        yield slog(step="main", status="error", error=str(e))

    yield slog(step="main", msg="Examples completed!")

    return None


# Protocol for the run_all_examples function
class RunAllExamplesProtocol(Protocol):
    """Protocol for run_all_examples function."""
    async def __call__(self, openai_api_key: str, gpt5_available: bool, /) -> None: ...


# Pinjected-compatible entry point
@injected(protocol=RunAllExamplesProtocol)
async def a_run_all_examples(
    openai_api_key: str,
    gpt5_available: bool,
    /,
) -> None:
    """
    Run all structured LLM examples.
    
    Args:
        openai_api_key: Your OpenAI API key
        gpt5_available: Whether GPT-5 models are available
    
    Usage:
        python -m pinjected run doeff_openai.examples.structured_llm_example.a_run_all_examples \
            --openai-api-key "sk-..." \
            --gpt5-available false
    """
    # Run with environment
    result = run(
        _run_all_examples_impl(),
        env={
            "openai_api_key": openai_api_key,
            "gpt5_available": gpt5_available,
        }
    )

    if result.is_err():
        print(f"Error: {result.error}")
        import traceback
        traceback.print_exception(
            type(result.error),
            result.error,
            result.error.__traceback__
        )

    # Print execution logs
    print("\nExecution Log:")
    for log_entry in result.log:
        if isinstance(log_entry, tuple) and log_entry[0] == "log":
            print(f"  {log_entry[1]}")


# Instance for GPT-5 availability
@instance
def gpt5_available() -> bool:
    """Whether GPT-5 models are available."""
    return False


# Instance for OpenAI API key (users should override this)
@instance
def openai_api_key() -> str:
    """OpenAI API key. Override with --openai-api-key when running."""
    return ""
