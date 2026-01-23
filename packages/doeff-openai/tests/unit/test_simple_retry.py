"""Simple test to debug retry tracking."""

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from doeff_openai import structured_llm__openai
from doeff_openai.client import OpenAIClient

from doeff import (
    Ask,
    AsyncRuntime,
    EffectGenerator,
    Tell,
    do,
)


@pytest.mark.asyncio
async def test_simple_success():
    """Test that successful calls work correctly."""

    # Mock OpenAI client
    mock_client = Mock(spec=OpenAIClient)
    mock_async_client = AsyncMock()
    mock_client.async_client = mock_async_client

    # Create a proper mock response
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Test response"
    mock_response.usage = MagicMock()
    mock_response.usage.total_tokens = 50
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 40

    # Set the return value (not side_effect)
    mock_async_client.chat.completions.create.return_value = mock_response

    @do
    def test_flow() -> EffectGenerator[str]:
        # Provide mock client in environment
        yield Ask("openai_client")

        # Log to see what's happening
        yield Tell("Starting API call")

        result = yield structured_llm__openai(
            text="Test prompt",
            model="gpt-4o",
            max_tokens=100,
            max_retries=1,  # Just one attempt
        )

        yield Tell(f"Got result: {result}")
        return result

    runtime = AsyncRuntime()

    # Run the test
    result = await runtime.run(test_flow(), env={"openai_client": mock_client})

    # Print all logs to debug
    print("\n=== LOGS ===")
    for i, log in enumerate(result.log):
        print(f"{i}: {log}")

    # Print state to see tracked calls
    print("\n=== STATE ===")
    if "openai_api_calls" in result.state:
        calls = result.state["openai_api_calls"]
        print(f"API calls tracked: {len(calls)}")
        for i, call in enumerate(calls):
            print(f"  Call {i}: error={call.get('error')}, tokens={call.get('tokens')}")

    # Check result
    if result.is_err():
        print("\n=== ERROR ===")
        print(result.error)

    assert result.is_ok()
    assert result.value == "Test response"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
