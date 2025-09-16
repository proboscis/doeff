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

from typing import List, Optional, Protocol
from pydantic import BaseModel, Field

from doeff import (
    do,
    EffectGenerator,
    Ask,
    Log,
    Catch,
)

from doeff_openai import (
    structured_llm__openai,
    gpt5_nano_structured,
    get_total_cost,
    reset_cost_tracking,
)

from pinjected import injected, instance


# Define structured output models
class CityInfo(BaseModel):
    """Information about a city."""
    name: str = Field(description="The city name")
    country: str = Field(description="The country where the city is located")
    population: int = Field(description="The population of the city")
    is_capital: bool = Field(description="Whether this city is a capital")
    famous_landmarks: List[str] = Field(description="List of famous landmarks")


class MathProblem(BaseModel):
    """Solution to a math problem."""
    problem: str = Field(description="The original problem statement")
    steps: List[str] = Field(description="Step-by-step solution")
    answer: float = Field(description="The final numerical answer")
    confidence: float = Field(description="Confidence in the answer (0-1)")


class CodeAnalysis(BaseModel):
    """Analysis of code snippet."""
    language: str = Field(description="Programming language")
    complexity: str = Field(description="Complexity level: simple, moderate, complex")
    issues: List[str] = Field(description="List of potential issues")
    improvements: List[str] = Field(description="Suggested improvements")
    has_security_issues: bool = Field(description="Whether there are security concerns")


# Example 1: Simple text response
@do
def example_plain_text() -> EffectGenerator[str]:
    """Example of getting a plain text response."""
    yield Log("Example 1: Plain text response")
    
    result = yield structured_llm__openai(
        text="What is the capital of France? Answer in one sentence.",
        model="gpt-4o",
        max_tokens=100,
        temperature=0.5,
    )
    
    yield Log(f"Response: {result}")
    return result


# Example 2: Structured output with Pydantic
@do
def example_structured_output() -> EffectGenerator[CityInfo]:
    """Example of getting structured output."""
    yield Log("Example 2: Structured output with Pydantic")
    
    result = yield structured_llm__openai(
        text="Tell me about Tokyo, Japan. Include population and famous landmarks.",
        model="gpt-4o",
        response_format=CityInfo,
        max_tokens=500,
        temperature=0.3,
    )
    
    yield Log(f"City: {result.name}")
    yield Log(f"Population: {result.population:,}")
    yield Log(f"Is Capital: {result.is_capital}")
    yield Log(f"Landmarks: {', '.join(result.famous_landmarks)}")
    
    return result


# Example 3: Math problem with optional GPT-5 
@do
def example_gpt5_reasoning() -> EffectGenerator[MathProblem]:
    """Example of using GPT-5 with reasoning mode for complex problem solving."""
    yield Log("Example 3: GPT-5 with thinking mode (if available)")
    
    # Check if GPT-5 is available via Ask effect
    gpt5_available = yield Ask("gpt5_available")
    model_name = "gpt-5-nano" if gpt5_available else "gpt-4o"
    
    yield Log(f"Using model: {model_name}")
    
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
    
    yield Log(f"Problem: {result.problem}")
    yield Log("Solution steps:")
    for i, step in enumerate(result.steps, 1):
        yield Log(f"  {i}. {step}")
    yield Log(f"Answer: {result.answer}")
    yield Log(f"Confidence: {result.confidence:.1%}")
    
    return result


# Example 4: Code analysis with error handling
@do
def example_code_analysis() -> EffectGenerator[Optional[CodeAnalysis]]:
    """Example of analyzing code with error handling."""
    yield Log("Example 4: Code analysis with error handling")
    
    code_snippet = '''
    def calculate_average(numbers):
        total = 0
        for num in numbers:
            total += num
        return total / len(numbers)
    '''
    
    # Using Catch effect for error handling
    from doeff import Fail
    
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
    
    @do
    def handle_error(error):
        yield Log(f"Error analyzing code: {error}")
        yield Log("Returning None as fallback")
        return None
    
    result = yield Catch(analyze_code(), handle_error)
    
    if result:
        yield Log(f"Language: {result.language}")
        yield Log(f"Complexity: {result.complexity}")
        yield Log(f"Issues: {result.issues}")
        yield Log(f"Improvements: {result.improvements}")
        yield Log(f"Security issues: {result.has_security_issues}")
    
    return result


# Example 5: Cost tracking
@do
def example_with_cost_tracking() -> EffectGenerator[None]:
    """Example showing cost tracking across multiple API calls."""
    yield Log("Example 5: Cost tracking")
    
    # Reset cost tracking for this example
    yield reset_cost_tracking()
    
    # Make several API calls
    yield Log("Making multiple API calls...")
    
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
    yield Log(f"Total cost for all API calls: ${total_cost:.6f}")
    
    return None


# Main program that runs all examples
@do
def _run_all_examples_impl() -> EffectGenerator[None]:
    """Run all examples in sequence."""
    yield Log("=" * 60)
    yield Log("DoEff OpenAI Structured LLM Examples")
    yield Log("=" * 60)
    
    # Check if API key is available
    api_key = yield Ask("openai_api_key")
    if not api_key:
        yield Log("ERROR: OPENAI_API_KEY not provided")
        yield Log("Please provide your OpenAI API key via dependency injection:")
        yield Log("  python -m pinjected run <module>.a_run_all_examples --openai-api-key 'sk-...'")
        return None
    
    # Run examples
    try:
        yield Log("\n")
        yield example_plain_text()
        
        yield Log("\n" + "-" * 40 + "\n")
        yield example_structured_output()
        
        yield Log("\n" + "-" * 40 + "\n")
        yield example_gpt5_reasoning()
        
        yield Log("\n" + "-" * 40 + "\n")
        yield example_code_analysis()
        
        yield Log("\n" + "-" * 40 + "\n")
        yield example_with_cost_tracking()
        
    except Exception as e:
        yield Log(f"Error running examples: {e}")
    
    yield Log("\n" + "=" * 60)
    yield Log("Examples completed!")
    
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
    from doeff import run_with_env
    
    # Run with environment
    result = await run_with_env(
        _run_all_examples_impl(),
        env={
            "openai_api_key": openai_api_key,
            "gpt5_available": gpt5_available,
        }
    )
    
    if result.is_err:
        print(f"Error: {result.result.error}")
        import traceback
        traceback.print_exception(
            type(result.result.error), 
            result.result.error, 
            result.result.error.__traceback__
        )
    
    # Print execution logs
    print("\nExecution Log:")
    for log_entry in result.log:
        if isinstance(log_entry, tuple) and log_entry[0] == 'log':
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