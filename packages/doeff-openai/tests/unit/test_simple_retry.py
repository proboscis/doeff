"""Simple test to debug retry tracking."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from doeff_openai import get_api_calls, structured_llm__openai
from doeff_openai.client import OpenAIClient

from doeff import (
    Ask,
    AskEffect,
    Delegate,
    EffectGenerator,
    Resume,
    Tell,
    WithHandler,
    async_run,
    default_handlers,
    do,
)


def _ask_override_handler(overrides: dict[str, object]):
    def handler(effect, k):
        if isinstance(effect, AskEffect) and effect.key in overrides:
            return (yield Resume(k, overrides[effect.key]))
        yield Delegate()

    return handler


@pytest.mark.asyncio
async def test_simple_success():
    """Test that successful calls work correctly."""

    # Mock OpenAI client
    mock_client = Mock(spec=OpenAIClient)
    mock_async_client = AsyncMock()
    mock_client.async_client = mock_async_client

    # Create a proper mock response
    mock_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Test response"))],
        usage=SimpleNamespace(
            total_tokens=50,
            prompt_tokens=10,
            completion_tokens=40,
        ),
    )

    # Set the return value (not side_effect)
    mock_async_client.chat.completions.create.return_value = mock_response

    @do
    def test_flow() -> EffectGenerator[dict[str, object]]:
        # Provide mock client in environment
        yield Ask("openai_client")

        # Log to see what's happening
        yield Tell("Starting API call")

        text_result = yield structured_llm__openai(
            text="Test prompt",
            model="gpt-4o",
            max_tokens=100,
            max_retries=1,  # Just one attempt
        )

        yield Tell(f"Got result: {text_result}")
        api_calls = yield get_api_calls()
        return {"text": text_result, "api_calls": api_calls}

    # Run the test
    handler = _ask_override_handler({"openai_client": mock_client})
    result = await async_run(
        WithHandler(handler, test_flow()),
        handlers=default_handlers(),
    )

    # Print all logs to debug
    print("\n=== LOGS ===")
    for i, log in enumerate(result.log):
        print(f"{i}: {log}")

    # Print state to see tracked calls
    print("\n=== STATE ===")
    calls = result.value["api_calls"] if result.is_ok() else []
    print(f"API calls tracked: {len(calls)}")
    for i, call in enumerate(calls):
        print(f"  Call {i}: error={call.get('error')}, tokens={call.get('tokens')}")

    # Check result
    if result.is_err():
        print("\n=== ERROR ===")
        print(result.error)

    assert result.is_ok()
    assert result.value["text"] == "Test response"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
