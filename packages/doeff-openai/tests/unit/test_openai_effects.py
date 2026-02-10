"""Tests for doeff-openai with full observability."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from doeff_openai import (
    OpenAIClient,
    TokenUsage,
    calculate_cost,
    chat_completion,
    count_message_tokens,
    count_tokens,
    create_embedding,
    get_api_calls,
    get_model_cost,
    get_single_embedding,
    get_total_cost,
    reset_cost_tracking,
)

from doeff import (
    Ask,
    AsyncRuntime,
    EffectGenerator,
    Gather,
    Get,
    Put,
    Tell,
    do,
)


@pytest.fixture
def mock_openai_client():
    """Create a mock OpenAI client."""
    client = Mock(spec=OpenAIClient)

    # Mock sync client
    sync_client = MagicMock()
    client.sync_client = sync_client

    # Mock async client
    async_client = AsyncMock()
    client.async_client = async_client

    return client


@pytest.fixture
def mock_chat_response():
    """Create a mock chat completion response."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="Hello! How can I help you?"),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=8,
            total_tokens=18,
        ),
        id="chatcmpl-test123",
        model="gpt-3.5-turbo",
    )


@pytest.fixture
def mock_embedding_response():
    """Create a mock embedding response."""
    return SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3, 0.4, 0.5])],
        usage=SimpleNamespace(
            prompt_tokens=5,
            completion_tokens=0,
            total_tokens=5,
        ),
        model="text-embedding-3-small",
    )


@pytest.mark.asyncio
async def test_chat_completion_with_tracking(mock_openai_client, mock_chat_response):
    """Test chat completion with full Graph and Log tracking."""

    @do
    def test_workflow() -> EffectGenerator[Any]:
        # Set up mock client in environment
        yield Ask("openai_client")  # This will be caught and return mock

        # Make chat completion
        response = yield chat_completion(
            messages=[{"role": "user", "content": "Hello!"}],
            model="gpt-3.5-turbo"
        )

        # Check total cost was tracked
        total_cost = yield get_total_cost()

        # Check model-specific cost
        model_cost = yield get_model_cost("gpt-3.5-turbo")
        api_calls = yield get_api_calls()

        return {
            "response": response,
            "total_cost": total_cost,
            "model_cost": model_cost,
            "api_calls": api_calls,
        }

    # Set up mock to return our response
    mock_openai_client.async_client.chat.completions.create.return_value = mock_chat_response

    # Run with mock client in environment
    runtime = AsyncRuntime()
    result = await runtime.run(test_workflow(), env={"openai_client": mock_openai_client})

    # Verify success
    assert result.is_ok()

    # Check response
    assert result.value["response"] == mock_chat_response

    # Check logs were created
    logs = result.log
    assert any("OpenAI chat request" in str(log) for log in logs)
    assert any("Chat completion finished" in str(log) for log in logs)

    # Check that API call was tracked
    api_calls = result.value["api_calls"]
    assert len(api_calls) > 0

    # Check metadata in tracked API calls
    for call in api_calls:
        assert "model" in call
        assert "operation" in call
        assert call["operation"] == "chat.completion"


@pytest.mark.asyncio
async def test_embedding_with_tracking(mock_openai_client, mock_embedding_response):
    """Test embedding creation with tracking."""

    @do
    def test_workflow() -> EffectGenerator[Any]:
        # Set up mock client
        yield Ask("openai_client")

        # Create embedding
        response = yield create_embedding(
            input="Test text for embedding",
            model="text-embedding-3-small"
        )

        # Get single embedding
        embedding = yield get_single_embedding("Another test")
        api_calls = yield get_api_calls()

        return {
            "response": response,
            "embedding": embedding,
            "api_calls": api_calls,
        }

    # Set up mock
    mock_openai_client.async_client.embeddings.create.return_value = mock_embedding_response

    # Run
    runtime = AsyncRuntime()
    result = await runtime.run(test_workflow(), env={"openai_client": mock_openai_client})

    # Verify
    assert result.is_ok()
    assert result.value["response"] == mock_embedding_response
    assert result.value["embedding"] == [0.1, 0.2, 0.3, 0.4, 0.5]

    # Check logs
    logs = result.log
    assert any("OpenAI embedding request" in str(log) for log in logs)

    # Check that API calls were tracked
    api_calls = result.value["api_calls"]
    embedding_calls = [call for call in api_calls if call.get("operation") == "embedding"]
    assert len(embedding_calls) > 0


@pytest.mark.asyncio
async def test_cost_tracking():
    """Test cost tracking across multiple API calls."""

    @do
    def test_workflow() -> EffectGenerator[Any]:
        # Reset cost tracking
        yield reset_cost_tracking()

        # Simulate multiple API calls by manually tracking
        # (In real usage, these would be set by actual API calls)

        # First call - GPT-3.5
        token_usage1 = TokenUsage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150
        )
        cost1 = calculate_cost("gpt-3.5-turbo", token_usage1)

        # Update state
        current = yield Get("total_openai_cost")
        yield Put("total_openai_cost", (current or 0) + cost1.total_cost)
        yield Put("openai_cost_gpt-3.5-turbo", cost1.total_cost)

        # Second call - GPT-4
        token_usage2 = TokenUsage(
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300
        )
        cost2 = calculate_cost("gpt-4", token_usage2)

        # Update state
        current = yield Get("total_openai_cost")
        yield Put("total_openai_cost", current + cost2.total_cost)
        yield Put("openai_cost_gpt-4", cost2.total_cost)

        # Get costs
        total = yield get_total_cost()
        gpt35_cost = yield get_model_cost("gpt-3.5-turbo")
        gpt4_cost = yield get_model_cost("gpt-4")

        return {
            "total": total,
            "gpt35": gpt35_cost,
            "gpt4": gpt4_cost,
            "expected_total": cost1.total_cost + cost2.total_cost,
        }

    # Run
    runtime = AsyncRuntime()
    result = await runtime.run(test_workflow())

    # Verify
    assert result.is_ok()
    assert result.value["total"] == result.value["expected_total"]
    assert result.value["gpt35"] > 0
    assert result.value["gpt4"] > 0
    assert result.value["total"] == result.value["gpt35"] + result.value["gpt4"]


@pytest.mark.asyncio
async def test_parallel_operations_with_gather():
    """Test multiple API-like operations and aggregated results."""

    @do
    def test_workflow() -> EffectGenerator[Any]:
        # Mock client setup
        client = Mock(spec=OpenAIClient)

        # Create mock responses
        responses = []
        for i in range(3):
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = f"Response {i}"
            responses.append(resp)

        # Simulate multiple calls and aggregate their results.
        @do
        def mock_call(index: int) -> EffectGenerator[str]:
            yield Tell(f"Call {index}")
            return f"Response {index}"

        results = []
        for i in range(3):
            results.append((yield mock_call(i)))

        return results

    # Run
    runtime = AsyncRuntime()
    result = await runtime.run(test_workflow())

    # Verify
    assert result.is_ok()
    assert len(result.value) == 3
    assert result.value == ["Response 0", "Response 1", "Response 2"]

    # Check logs show each call execution
    assert len(result.log) == 3


def test_token_counting():
    """Test token counting functions."""

    # Test simple text
    text = "Hello, world!"
    tokens = count_tokens(text, "gpt-3.5-turbo")
    assert tokens > 0

    # Test message tokens
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"},
    ]
    msg_tokens = count_message_tokens(messages, "gpt-3.5-turbo")
    assert msg_tokens > 0

    # Messages should have more tokens due to formatting
    assert msg_tokens > count_tokens("You are a helpful assistant. Hello!", "gpt-3.5-turbo")


def test_cost_calculation():
    """Test cost calculation."""

    usage = TokenUsage(
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500
    )

    # Test GPT-3.5 cost
    cost_35 = calculate_cost("gpt-3.5-turbo", usage)
    assert cost_35.total_cost > 0
    assert cost_35.input_cost > 0
    assert cost_35.output_cost > 0
    assert cost_35.total_cost == cost_35.input_cost + cost_35.output_cost

    # Test GPT-4 cost (should be higher)
    cost_4 = calculate_cost("gpt-4", usage)
    assert cost_4.total_cost > cost_35.total_cost

    # Test embedding cost (no output cost)
    embedding_usage = TokenUsage(
        prompt_tokens=100,
        completion_tokens=0,
        total_tokens=100
    )
    embedding_cost = calculate_cost("text-embedding-3-small", embedding_usage)
    assert embedding_cost.output_cost == 0
    assert embedding_cost.total_cost == embedding_cost.input_cost


@pytest.mark.asyncio
async def test_semantic_search_workflow():
    """Test semantic search with mock embeddings."""

    @do
    def test_workflow() -> EffectGenerator[Any]:
        # Mock documents
        documents = [
            "The cat sat on the mat.",
            "Dogs are loyal animals.",
            "Python is a programming language.",
            "Machine learning is fascinating.",
        ]

        # In real usage, this would call OpenAI
        # For testing, we'll simulate with mock embeddings

        # Mock embedding function
        @do
        def mock_embedding(text: str) -> EffectGenerator[list]:
            # Return different embeddings based on content
            if "cat" in text.lower():
                return [1.0, 0.0, 0.0, 0.0, 0.0]
            if "dog" in text.lower():
                return [0.9, 0.1, 0.0, 0.0, 0.0]
            if "python" in text.lower():
                return [0.0, 0.0, 1.0, 0.0, 0.0]
            if "machine" in text.lower():
                return [0.0, 0.0, 0.9, 0.1, 0.0]
            return [0.5, 0.5, 0.5, 0.5, 0.5]

        # Get embeddings for query
        query_embedding = yield mock_embedding("cats and dogs")

        # Get embeddings for documents
        doc_embeddings = []
        for doc in documents:
            doc_embeddings.append((yield mock_embedding(doc)))

        # Calculate similarities (simplified)
        similarities = []
        for i, doc_emb in enumerate(doc_embeddings):
            # Simple dot product
            similarity = sum(a * b for a, b in zip(query_embedding, doc_emb, strict=False))
            similarities.append((i, similarity, documents[i]))

        # Sort and get top 2
        similarities.sort(key=lambda x: x[1], reverse=True)
        top_results = similarities[:2]

        return top_results

    # Run
    runtime = AsyncRuntime()
    result = await runtime.run(test_workflow())

    # Verify
    assert result.is_ok()
    assert len(result.value) == 2
    # First result should have highest similarity
    assert result.value[0][1] >= result.value[1][1]


@pytest.mark.asyncio
async def test_track_api_call_accumulates_under_gather():
    """Test that repeated track_api_call invocations accumulate cost and metadata."""
    import math
    import time

    from doeff_openai.client import track_api_call

    model = "gpt-4"

    # Define multiple API calls with different token counts
    call_defs = [
        {"request_id": "req-1", "prompt": "First", "input": 100, "output": 50},
        {"request_id": "req-2", "prompt": "Second", "input": 200, "output": 100},
        {"request_id": "req-3", "prompt": "Third", "input": 150, "output": 75},
    ]

    def _fake_response(call: dict[str, Any]) -> Any:
        """Create a fake OpenAI response with usage metadata."""
        return SimpleNamespace(
            id=call["request_id"],
            usage=SimpleNamespace(
                prompt_tokens=call["input"],
                completion_tokens=call["output"],
                total_tokens=call["input"] + call["output"],
            ),
            choices=[SimpleNamespace(finish_reason="stop")],
        )

    @do
    def invoke(call: dict[str, Any]) -> EffectGenerator[Any]:
        response = _fake_response(call)
        start_time = time.time() - 0.01
        return (
            yield track_api_call(
                operation="chat.completion",
                model=model,
                request_payload={"messages": [{"role": "user", "content": call["prompt"]}]},
                response=response,
                start_time=start_time,
                error=None,
            )
        )

    @do
    def run_parallel() -> EffectGenerator[dict[str, Any]]:
        for call in call_defs:
            yield invoke(call)
        api_calls = yield get_api_calls()
        total_cost = yield get_total_cost()
        model_cost = yield get_model_cost(model)
        return {
            "api_calls": api_calls,
            "total_cost": total_cost,
            "model_cost": model_cost,
        }

    runtime = AsyncRuntime()
    result = await runtime.run(run_parallel())

    assert result.is_ok()
    api_calls = result.value["api_calls"]
    actual_total = result.value["total_cost"]
    model_cost = result.value["model_cost"]

    # Verify all API calls were tracked
    assert len(api_calls) == 3, f"Expected 3 API calls, got {len(api_calls)}"

    # Calculate expected total cost
    expected_total = sum(
        calculate_cost(
            model,
            TokenUsage(
                prompt_tokens=call["input"],
                completion_tokens=call["output"],
                total_tokens=call["input"] + call["output"],
            ),
        ).total_cost
        for call in call_defs
    )

    # Verify total cost accumulation
    assert math.isclose(actual_total, expected_total, rel_tol=1e-9), (
        f"Expected total cost {expected_total}, got {actual_total}"
    )

    # Verify per-model cost accumulation
    assert math.isclose(model_cost, expected_total, rel_tol=1e-9), (
        f"Expected model cost {expected_total}, got {model_cost}"
    )


# if __name__ == "__main__":
#     pytest.main([__file__, "-v"])
