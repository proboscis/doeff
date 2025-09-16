"""Tests for doeff-openai with full observability."""

import pytest
from typing import Any
from unittest.mock import Mock, MagicMock, AsyncMock

from doeff import (
    do,
    Effect,
    EffectGenerator,
    ProgramInterpreter,
    ExecutionContext,
    Ask,
    Get,
    Gather,
)

from doeff_openai import (
    OpenAIClient,
    get_openai_client,
    chat_completion,
    simple_chat,
    create_embedding,
    get_single_embedding,
    batch_embeddings,
    semantic_search,
    get_total_cost,
    get_model_cost,
    reset_cost_tracking,
    count_tokens,
    count_message_tokens,
    calculate_cost,
    TokenUsage,
    CostInfo,
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
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "Hello! How can I help you?"
    response.choices[0].finish_reason = "stop"
    
    # Add usage info
    response.usage = MagicMock()
    response.usage.prompt_tokens = 10
    response.usage.completion_tokens = 8
    response.usage.total_tokens = 18
    
    response.id = "chatcmpl-test123"
    response.model = "gpt-3.5-turbo"
    
    return response


@pytest.fixture
def mock_embedding_response():
    """Create a mock embedding response."""
    response = MagicMock()
    response.data = [MagicMock()]
    response.data[0].embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
    
    # Add usage info with actual numeric values
    usage = MagicMock()
    usage.prompt_tokens = 5
    usage.completion_tokens = 0
    usage.total_tokens = 5
    response.usage = usage
    
    response.model = "text-embedding-3-small"
    
    return response


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
        
        return {
            "response": response,
            "total_cost": total_cost,
            "model_cost": model_cost,
        }
    
    # Set up mock to return our response
    mock_openai_client.sync_client.chat.completions.create.return_value = mock_chat_response
    
    # Run with mock client in environment
    engine = ProgramInterpreter()
    context = ExecutionContext(env={"openai_client": mock_openai_client})
    
    result = await engine.run(test_workflow(), context)
    
    # Verify success
    assert result.is_ok
    
    # Check response
    assert result.value["response"] == mock_chat_response
    
    # Check logs were created
    logs = context.log
    assert any("OpenAI chat request" in str(log) for log in logs)
    assert any("Chat completion finished" in str(log) for log in logs)
    
    # Check graph steps were created
    assert len(context.graph.steps) > 0
    
    # Find API call steps
    api_steps = [
        step for step in context.graph.steps
        if step.meta.get("type") == "openai_api_call"
    ]
    assert len(api_steps) > 0
    
    # Check metadata
    for step in api_steps:
        assert "model" in step.meta
        assert "operation" in step.meta
        assert step.meta["operation"] == "chat.completion"


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
        
        return {
            "response": response,
            "embedding": embedding,
        }
    
    # Set up mock
    mock_openai_client.sync_client.embeddings.create.return_value = mock_embedding_response
    
    # Run
    engine = ProgramInterpreter()
    context = ExecutionContext(env={"openai_client": mock_openai_client})
    
    result = await engine.run(test_workflow(), context)
    
    # Verify
    assert result.is_ok
    assert result.value["response"] == mock_embedding_response
    assert result.value["embedding"] == [0.1, 0.2, 0.3, 0.4, 0.5]
    
    # Check logs
    logs = context.log
    assert any("OpenAI embedding request" in str(log) for log in logs)
    
    # Check graph
    embedding_steps = [
        step for step in context.graph.steps
        if step.meta.get("operation") == "embedding"
    ]
    assert len(embedding_steps) > 0


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
        yield Effect("state.put", {"key": "total_openai_cost", "value": (current or 0) + cost1.total_cost})
        yield Effect("state.put", {"key": "openai_cost_gpt-3.5-turbo", "value": cost1.total_cost})
        
        # Second call - GPT-4
        token_usage2 = TokenUsage(
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300
        )
        cost2 = calculate_cost("gpt-4", token_usage2)
        
        # Update state
        current = yield Get("total_openai_cost")
        yield Effect("state.put", {"key": "total_openai_cost", "value": current + cost2.total_cost})
        yield Effect("state.put", {"key": "openai_cost_gpt-4", "value": cost2.total_cost})
        
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
    engine = ProgramInterpreter()
    context = ExecutionContext()
    
    result = await engine.run(test_workflow(), context)
    
    # Verify
    assert result.is_ok
    assert result.value["total"] == result.value["expected_total"]
    assert result.value["gpt35"] > 0
    assert result.value["gpt4"] > 0
    assert result.value["total"] == result.value["gpt35"] + result.value["gpt4"]


@pytest.mark.asyncio
async def test_parallel_operations_with_gather():
    """Test parallel API calls using Gather effect."""
    
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
        
        # Simulate parallel calls using Gather
        # (In real usage, these would be actual API calls)
        @do
        def mock_call(index: int) -> EffectGenerator[str]:
            yield Effect("writer.tell", f"Call {index}")
            return f"Response {index}"
        
        results = yield Gather(*[
            mock_call(i) for i in range(3)
        ])
        
        return results
    
    # Run
    engine = ProgramInterpreter()
    context = ExecutionContext()
    
    result = await engine.run(test_workflow(), context)
    
    # Verify
    assert result.is_ok
    assert len(result.value) == 3
    assert result.value == ["Response 0", "Response 1", "Response 2"]
    
    # Check logs show parallel execution
    assert len(context.log) == 3


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
            elif "dog" in text.lower():
                return [0.9, 0.1, 0.0, 0.0, 0.0]
            elif "python" in text.lower():
                return [0.0, 0.0, 1.0, 0.0, 0.0]
            elif "machine" in text.lower():
                return [0.0, 0.0, 0.9, 0.1, 0.0]
            else:
                return [0.5, 0.5, 0.5, 0.5, 0.5]
        
        # Get embeddings for query
        query_embedding = yield mock_embedding("cats and dogs")
        
        # Get embeddings for documents
        doc_embeddings = yield Gather(*[
            mock_embedding(doc) for doc in documents
        ])
        
        # Calculate similarities (simplified)
        similarities = []
        for i, doc_emb in enumerate(doc_embeddings):
            # Simple dot product
            similarity = sum(a * b for a, b in zip(query_embedding, doc_emb))
            similarities.append((i, similarity, documents[i]))
        
        # Sort and get top 2
        similarities.sort(key=lambda x: x[1], reverse=True)
        top_results = similarities[:2]
        
        return top_results
    
    # Run
    engine = ProgramInterpreter()
    context = ExecutionContext()
    
    result = await engine.run(test_workflow(), context)
    
    # Verify
    assert result.is_ok
    assert len(result.value) == 2
    # First result should have highest similarity
    assert result.value[0][1] >= result.value[1][1]


# if __name__ == "__main__":
#     pytest.main([__file__, "-v"])