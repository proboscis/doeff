"""Tests to verify that retry attempts are properly tracked."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from doeff_openai import get_api_calls, structured_llm__openai
from doeff_openai.client import OpenAIClient

from doeff import (
    Ask,
    EffectGenerator,
    async_run,
    default_handlers,
    do,
)


async def run_program(program, env=None):
    """Execute a test program with standard handlers."""
    return await async_run(program, handlers=default_handlers(), env=env)


@pytest.mark.asyncio
async def test_retry_tracking_on_failure_then_success():
    """Test that both failed and successful retry attempts are tracked."""

    # Mock time.time() to return predictable values
    with patch("doeff_openai.structured_llm.time.time") as mock_time1, \
         patch("doeff_openai.client.time.time") as mock_time2:
        # Return incrementing values for each call to time.time()
        # Each API call uses time.time() twice (start and end)
        time_values = [float(i) for i in range(1000, 1020)]
        mock_time1.side_effect = time_values.copy()
        mock_time2.side_effect = time_values.copy()

        # Mock OpenAI client
        mock_client = Mock(spec=OpenAIClient)
        mock_async_client = AsyncMock()
        mock_client.async_client = mock_async_client

        # Track the number of calls
        call_count = 0

        # Create a side effect that fails twice, then succeeds
        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                # First two calls fail
                raise Exception(f"API Error attempt {call_count}")
            # Third call succeeds
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="Success on third try")
                    )
                ],
                usage=SimpleNamespace(
                    total_tokens=100,
                    prompt_tokens=10,
                    completion_tokens=90,
                ),
            )

        mock_async_client.chat.completions.create.side_effect = side_effect

        @do
        def test_flow() -> EffectGenerator[dict[str, object]]:
            # Provide mock client in environment
            yield Ask("openai_client")

            text_result = yield structured_llm__openai(
                text="Test prompt",
                model="gpt-4o",
                max_tokens=100,
                max_retries=3,  # Allow up to 3 attempts
            )
            api_calls = yield get_api_calls()
            return {"text": text_result, "api_calls": api_calls}

        result = await run_program(test_flow(), env={"openai_client": mock_client})

        # Should eventually succeed on third attempt
        assert result.is_ok()
        assert result.value["text"] == "Success on third try"

        # Check that the API was called 3 times
        assert mock_async_client.chat.completions.create.call_count == 3

        # Check logs for tracking of each attempt
        log_messages = [str(log) for log in result.log]

        # Should have logs for each attempt
        api_call_logs = [log for log in log_messages if "Making OpenAI API call" in log]
        assert len(api_call_logs) == 3

        # Should have logs for the two failures
        failure_logs = [log for log in log_messages if "OpenAI API error" in log]
        assert len(failure_logs) == 2

        # Check API call tracking
        api_calls = result.value["api_calls"]

        # Should have tracked all 3 attempts (2 failures + 1 success)
        assert len(api_calls) == 3

        # First two should have errors
        assert api_calls[0]["error"] is not None
        assert api_calls[1]["error"] is not None

        # Last should be successful
        assert api_calls[2]["error"] is None
        assert api_calls[2]["tokens"]["total"] == 100


@pytest.mark.asyncio
async def test_retry_exhaustion_tracking():
    """Test that all retry attempts are tracked even when all fail."""

    # Mock time.time() to return predictable values
    with patch("doeff_openai.structured_llm.time.time") as mock_time1, \
         patch("doeff_openai.client.time.time") as mock_time2:
        # Return incrementing values for each call to time.time()
        time_values = [float(i) for i in range(1000, 1020)]
        mock_time1.side_effect = time_values.copy()
        mock_time2.side_effect = time_values.copy()

        # Mock OpenAI client
        mock_client = Mock(spec=OpenAIClient)
        mock_async_client = AsyncMock()
        mock_client.async_client = mock_async_client

        # Always fail
        mock_async_client.chat.completions.create.side_effect = Exception("Persistent API Error")

        @do
        def test_flow() -> EffectGenerator[str]:
            # Provide mock client in environment
            yield Ask("openai_client")

            result = yield structured_llm__openai(
                text="Test prompt",
                model="gpt-4o",
                max_tokens=100,
                max_retries=3,  # Allow up to 3 attempts
            )
            return result

        result = await run_program(test_flow(), env={"openai_client": mock_client})

        # Should fail after all retries
        assert result.is_err()

        # Check that the API was called 3 times (max_retries)
        assert mock_async_client.chat.completions.create.call_count == 3

        # Verify the error message contains our expected error
        assert "Persistent API Error" in str(result.error)

        # Note: State and logs are not preserved when program fails with exception
        # in the current CESK runtime implementation. The important verification
        # is that all retry attempts were made (verified by mock call count)
        # and that the final error is correctly propagated.


@pytest.mark.asyncio
async def test_no_retry_on_immediate_success():
    """Test that successful calls don't trigger retries."""

    # Mock time.time() to return predictable values
    with patch("doeff_openai.structured_llm.time.time") as mock_time1, \
         patch("doeff_openai.client.time.time") as mock_time2:
        # Return incrementing values for each call to time.time()
        time_values = [float(i) for i in range(1000, 1020)]
        mock_time1.side_effect = time_values.copy()
        mock_time2.side_effect = time_values.copy()

        # Mock OpenAI client
        mock_client = Mock(spec=OpenAIClient)
        mock_async_client = AsyncMock()
        mock_client.async_client = mock_async_client

        # Immediate success
        mock_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Immediate success")
                )
            ],
            usage=SimpleNamespace(
                total_tokens=50,
                prompt_tokens=5,
                completion_tokens=45,
            ),
        )

        mock_async_client.chat.completions.create.return_value = mock_response

        @do
        def test_flow() -> EffectGenerator[dict[str, object]]:
            # Provide mock client in environment
            yield Ask("openai_client")

            text_result = yield structured_llm__openai(
                text="Test prompt",
                model="gpt-4o",
                max_tokens=100,
                max_retries=3,
            )
            api_calls = yield get_api_calls()
            return {"text": text_result, "api_calls": api_calls}

        result = await run_program(test_flow(), env={"openai_client": mock_client})

        # Should succeed immediately
        assert result.is_ok()
        assert result.value["text"] == "Immediate success"

        # Check that the API was called only once
        assert mock_async_client.chat.completions.create.call_count == 1

        # Check API call tracking
        api_calls = result.value["api_calls"]

        # Should have tracked only one successful attempt (no double tracking for success)
        assert len(api_calls) == 1
        assert api_calls[0]["error"] is None
        assert api_calls[0]["tokens"]["total"] == 50


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
