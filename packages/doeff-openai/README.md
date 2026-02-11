# doeff-openai

OpenAI integration for [doeff](https://github.com/CyberAgentAILab/doeff) with comprehensive observability through Graph and Log effects.

## Features

- 🔍 **Full Observability**: Every API call is tracked via Graph effects with complete metadata
- 💰 **Cost Tracking**: Automatic token counting and cost calculation for all operations
- 📊 **Graph-based Audit Trail**: All API interactions become Graph steps for analysis
- 📝 **Detailed Logging**: Comprehensive logging via Log effects
- ⚡ **Streaming Support**: Full streaming support with incremental cost tracking
- 🔄 **Parallel Operations**: Use Gather effect for parallel API calls
- 🎯 **Type-Safe**: Fully typed with dataclasses and type hints

## Unified Effects Routing

`doeff-openai` now handles provider-agnostic effects from `doeff-llm`:

- `LLMChat`
- `LLMStreamingChat`
- `LLMStructuredOutput`
- `LLMEmbedding`

The handler checks `effect.model`. Supported OpenAI model prefixes are
`gpt-`, `o1-`, `o3-`, `o4-`, and `text-embedding-`. Unsupported models are
delegated with `Delegate()` so outer handlers can process them.

## Installation

```bash
pip install doeff-openai
```

## Quick Start

```python
from doeff import do, ProgramInterpreter, ExecutionContext
from doeff_openai import chat_completion, get_total_cost

@do
def ai_workflow():
    # API key comes from Reader environment
    response = yield chat_completion(
        messages=[{"role": "user", "content": "Hello!"}],
        model="gpt-3.5-turbo"
    )
    
    # Check accumulated cost
    total_cost = yield get_total_cost()
    print(f"Total cost so far: ${total_cost:.4f}")
    
    return response

# Run with API key in environment
async def main():
    engine = ProgramInterpreter()
    context = ExecutionContext(env={"openai_api_key": "sk-..."})
    result = await engine.run(ai_workflow(), context)
    
    # Access logs and graph for observability
    print(f"Logs: {context.log}")
    print(f"Graph steps: {len(context.graph.steps)}")
```

## Core Concepts

### Graph Effect for Observability

Every OpenAI API call creates Graph steps with rich metadata:

```python
@do
def tracked_completion():
    response = yield chat_completion(
        messages=[{"role": "user", "content": "Explain quantum computing"}],
        model="gpt-4",
        temperature=0.7,
        max_tokens=200
    )
    
    # The Graph now contains:
    # - Request step with metadata (model, tokens, timestamp)
    # - Response step with metadata (cost, latency, finish_reason)
    
    return response
```

Graph metadata structure:
```python
{
    "type": "openai_api_call",
    "operation": "chat.completion",
    "model": "gpt-4",
    "input_tokens": 15,
    "output_tokens": 189,
    "total_tokens": 204,
    "cost_usd": 0.00612,
    "latency_ms": 1523.4,
    "timestamp": "2024-01-15T10:30:00Z",
    "request_id": "chatcmpl-abc123",
    "finish_reason": "stop"
}
```

### Cost Tracking

Costs are automatically tracked in State:

```python
@do
def cost_aware_workflow():
    # Reset tracking for new session
    yield reset_cost_tracking()
    
    # Multiple API calls
    yield simple_chat("What is Python?")
    yield simple_chat("What is JavaScript?", model="gpt-4")
    yield create_embedding("Sample text")
    
    # Get costs
    total = yield get_total_cost()
    gpt4_cost = yield get_model_cost("gpt-4")
    
    print(f"Total: ${total:.4f}")
    print(f"GPT-4: ${gpt4_cost:.4f}")
```

### Analyzing the Graph

You can traverse the Graph to analyze all API interactions:

```python
@do
def analyze_api_usage():
    # ... make various API calls ...
    
    # Get the execution context to analyze
    context = yield Ask("__context__")  # Special key for context
    
    # Find all OpenAI API calls
    api_calls = [
        step for step in context.graph.steps
        if step.meta.get("type") == "openai_api_call"
    ]
    
    # Calculate total cost from graph
    total_cost = sum(
        step.meta.get("cost_usd", 0)
        for step in api_calls
    )
    
    # Find slowest call
    slowest = max(
        api_calls,
        key=lambda s: s.meta.get("latency_ms", 0)
    )
    
    return {
        "total_calls": len(api_calls),
        "total_cost": total_cost,
        "slowest_call": slowest.meta
    }
```

## API Reference

### Chat Completions

```python
@do
def chat_example():
    # Basic completion
    response = yield chat_completion(
        messages=[{"role": "user", "content": "Hello"}],
        model="gpt-3.5-turbo"
    )
    
    # With all parameters
    response = yield chat_completion(
        messages=messages,
        model="gpt-4",
        temperature=0.7,
        max_tokens=200,
        top_p=0.9,
        frequency_penalty=0.5,
        presence_penalty=0.5,
        stop=["\n\n"],
        tools=tools,
        tool_choice="auto"
    )
    
    # Simple interface
    text = yield simple_chat(
        "Explain recursion",
        model="gpt-4",
        system_prompt="You are a teacher"
    )
    
    # Async version
    response = yield chat_completion_async(messages, model="gpt-4")
```

### Embeddings

```python
@do
def embedding_example():
    # Single embedding
    response = yield create_embedding(
        "Text to embed",
        model="text-embedding-3-small"
    )
    
    # Batch embeddings with parallel processing
    embeddings = yield batch_embeddings(
        texts=["Text 1", "Text 2", "Text 3"],
        model="text-embedding-3-large",
        batch_size=100
    )
    
    # Get just the vector
    vector = yield get_single_embedding("Sample text")
    
    # Calculate similarity
    similarity = yield cosine_similarity(
        "Text A",
        "Text B",
        model="text-embedding-3-small"
    )
    
    # Semantic search
    results = yield semantic_search(
        query="machine learning",
        documents=documents,
        model="text-embedding-3-large",
        top_k=5
    )
```

### Streaming

```python
@do
def streaming_example():
    # Get streaming response
    stream = yield chat_completion(
        messages=messages,
        model="gpt-3.5-turbo",
        stream=True
    )
    
    # Process stream with full tracking
    content, tokens, cost = yield process_stream(
        stream,
        model="gpt-3.5-turbo",
        callback=lambda chunk: print(chunk, end="")
    )
    
    # Stream with metadata
    metadata_stream = yield stream_with_metadata(stream, model)
    
    # Buffered stream for efficiency
    buffered = yield buffered_stream(
        stream,
        model="gpt-3.5-turbo",
        buffer_size=5,
        buffer_time_ms=100
    )
```

### Parallel Operations

```python
@do
def parallel_example():
    # Process multiple prompts in parallel
    responses = yield Gather([
        simple_chat(f"Explain {topic}")
        for topic in ["Python", "JavaScript", "Rust"]
    ])
    
    # Parallel embeddings and chat
    results = yield Gather([
        create_embedding("Text to embed"),
        chat_completion(messages, model="gpt-4"),
        semantic_search(query, documents)
    ])
```

## Cost Management

### Token Counting

```python
from doeff_openai import count_tokens, count_message_tokens

# Count tokens in text
tokens = count_tokens("Hello, world!", "gpt-3.5-turbo")

# Count tokens in messages
messages = [
    {"role": "system", "content": "You are helpful"},
    {"role": "user", "content": "Hello!"}
]
tokens = count_message_tokens(messages, "gpt-4")
```

### Cost Estimation

```python
from doeff_openai import estimate_cost, estimate_max_cost

# Estimate cost for known tokens
cost_info = estimate_cost(
    model="gpt-4",
    input_tokens=100,
    output_tokens=200
)

# Estimate maximum cost
max_cost = estimate_max_cost(
    model="gpt-4",
    max_tokens=1000,
    messages=messages
)
```

### Model Pricing

```python
from doeff_openai import MODEL_PRICING, get_model_pricing

# Get pricing for a model
pricing = get_model_pricing("gpt-4")
print(f"Input: ${pricing.input_price_per_1k}/1K tokens")
print(f"Output: ${pricing.output_price_per_1k}/1K tokens")
```

## Complete Example

Here's a complete example showing all observability features:

```python
from doeff import do, ProgramInterpreter, ExecutionContext
from doeff_openai import (
    chat_completion,
    create_embedding,
    semantic_search,
    get_total_cost,
    reset_cost_tracking,
    Gather,
)

@do
def rag_pipeline(query: str, documents: list[str]):
    """RAG pipeline with full observability."""
    
    # Reset cost tracking for this session
    yield reset_cost_tracking()
    
    # Log start
    yield Log(f"Starting RAG pipeline for query: {query}")
    
    # Step 1: Find relevant documents
    search_results = yield semantic_search(
        query=query,
        documents=documents,
        model="text-embedding-3-small",
        top_k=3
    )
    
    # Step 2: Create context from top documents
    context = "\n\n".join([
        doc for _, _, doc in search_results
    ])
    
    # Step 3: Generate response with context
    messages = [
        {"role": "system", "content": "Answer based on the provided context."},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"}
    ]
    
    response = yield chat_completion(
        messages=messages,
        model="gpt-4",
        temperature=0.3,
        max_tokens=500
    )
    
    # Step 4: Get metrics
    total_cost = yield get_total_cost()
    
    # Log completion
    yield Log(f"RAG pipeline complete. Total cost: ${total_cost:.4f}")
    
    # Create final graph step summarizing the pipeline
    yield Step(
        {
            "pipeline": "rag",
            "query": query,
            "documents_searched": len(documents),
            "documents_used": len(search_results),
            "total_cost": total_cost,
        },
        {
            "type": "pipeline_complete",
            "name": "rag",
            "cost_usd": total_cost,
        }
    )
    
    return response.choices[0].message.content

# Run the pipeline
async def main():
    documents = [
        "Python is a high-level programming language.",
        "Machine learning is a subset of AI.",
        "Neural networks are inspired by the brain.",
        # ... more documents
    ]
    
    engine = ProgramInterpreter()
    context = ExecutionContext(
        env={"openai_api_key": "sk-..."}
    )
    
    result = await engine.run(
        rag_pipeline("What is Python?", documents),
        context
    )
    
    if result.is_ok:
        print(f"Answer: {result.value}")
        
        # Analyze the execution
        api_calls = [
            step for step in context.graph.steps
            if step.meta.get("type") == "openai_api_call"
        ]
        
        print(f"\nAPI Calls Made: {len(api_calls)}")
        for call in api_calls:
            print(f"  - {call.meta['operation']}: {call.meta['model']}")
            print(f"    Tokens: {call.meta.get('total_tokens', 'N/A')}")
            print(f"    Cost: ${call.meta.get('cost_usd', 0):.4f}")
            print(f"    Latency: {call.meta.get('latency_ms', 0):.0f}ms")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

## Configuration

### API Key Setup

The API key can be provided in multiple ways (in order of precedence):

1. Reader environment: `env={"openai_api_key": "sk-..."}`
2. State: Set via `Put("openai_api_key", "sk-...")`
3. Direct client creation with key

### Client Configuration

```python
@do
def custom_client():
    # Create custom client
    client = OpenAIClient(
        api_key="sk-...",
        organization="org-...",
        base_url="https://custom.openai.com",
        timeout=30.0,
        max_retries=3
    )
    
    # Store in state for reuse
    yield Put("openai_client", client)
    
    # Now all API calls will use this client
    response = yield chat_completion(messages, model="gpt-4")
```

## Testing

The package includes comprehensive tests showing mocking patterns:

```python
import pytest
from unittest.mock import Mock, MagicMock
from doeff_openai import OpenAIClient

@pytest.fixture
def mock_openai_client():
    client = Mock(spec=OpenAIClient)
    # ... configure mock
    return client

@do
def test_workflow():
    # Mock client will be injected
    response = yield chat_completion(messages, model="gpt-4")
    # ... test assertions
```

## License

MIT License - see LICENSE file for details.
