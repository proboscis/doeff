"""Simple test to debug retry tracking."""

import pytest
from typing import Any
from unittest.mock import Mock, MagicMock, AsyncMock
import time

from doeff import (
    do,
    EffectGenerator,
    ProgramInterpreter,
    ExecutionContext,
    Ask,
    Log,
)

from doeff_openai import structured_llm__openai
from doeff_openai.client import OpenAIClient


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
        yield Log("Starting API call")
        
        result = yield structured_llm__openai(
            text="Test prompt",
            model="gpt-4o",
            max_tokens=100,
            max_retries=1,  # Just one attempt
        )
        
        yield Log(f"Got result: {result}")
        return result
    
    engine = ProgramInterpreter()
    context = ExecutionContext(env={"openai_client": mock_client})
    
    # Run the test
    result = await engine.run(test_flow(), context)
    
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
    if result.is_err:
        print(f"\n=== ERROR ===")
        print(result.result.error)
    
    assert result.is_ok
    assert result.value == "Test response"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])