# doeff-openai

OpenAI integration for [doeff](https://github.com/proboscis/doeff) with comprehensive observability through Graph and Log effects.

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
- `LLMStructuredQuery`
- `LLMEmbedding`

The handler checks `effect.model`. Supported OpenAI model prefixes are
`gpt-`, `o1-`, `o3-`, `o4-`, and `text-embedding-`. Unsupported models are
delegated with `Delegate()` so outer handlers can process them.

## Waiting between retries

Retries of chat completions, embeddings and structured calls wait with doeff-time's `Delay` effect rather than sleeping, so the caller's time handler
decides what a second is. Install `doeff_time.async_time_handler()` (the wall clock) in the handler
stack, inside `await_handler`, which answers its `Await`; a test installs `sim_time_handler()`, whose virtual clock moves at once, and the retry path
runs without sleeping. Without a time handler, a retry fails with an unhandled `DelayEffect`.

## Installation

```bash
pip install doeff-openai
```

## Quick Start

```python
from doeff import do, ProgramInterpreter, ExecutionContext
from doeff_openai._chat import chat_completion
from doeff_openai._client import get_total_cost

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

The pricing table and the token counters are inside the package (ADR-DOE-API-001 R1). A program
asks for the cost of a call with the `CalculateCost` effect, and a handler answers it. This
package's handlers `openai_production_handler` and `calculate_cost_handler` answer from the
package's pricing table and pass on the models the table does not know, so an outer handler of
yours can answer those (or replace the table altogether).

```python
from doeff import Pass, Resume, do, handler
from doeff_openai import CalculateCost, CostInfo, TokenUsage, calculate_cost_handler

@do
def price_call():
    usage = TokenUsage(prompt_tokens=100, completion_tokens=200, total_tokens=300)
    cost_info = yield CalculateCost(model="gpt-4", token_usage=usage)
    return cost_info.total_cost

@handler
def in_house_prices(effect, k):
    if isinstance(effect, CalculateCost) and effect.model == "my-finetune":
        usage = effect.token_usage
        cost = usage.total_tokens / 1000 * 0.002
        cost_info = CostInfo(
            input_cost=cost,
            output_cost=0.0,
            total_cost=cost,
            model=effect.model,
            token_usage=usage,
        )
        return (yield Resume(k, cost_info))
    return (yield Pass(effect, k))

program = in_house_prices(calculate_cost_handler(price_call()))
```

## Complete Example

Here's a complete example showing all observability features:

```python
from doeff import do, ProgramInterpreter, ExecutionContext
from doeff_openai import Gather
from doeff_openai._chat import chat_completion
from doeff_openai._client import get_total_cost, reset_cost_tracking
from doeff_openai._embeddings import create_embedding, semantic_search

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

### Client Configuration

The OpenAI client class is inside the handlers (ADR-DOE-API-001 R1): the handler builds the client
the first time a call needs it, from these Reader keys, and keeps it in State for the later calls.
A missing key leaves the OpenAI SDK's default.

| Reader key | Client setting |
|---|---|
| `openai_api_key` | API key |
| `openai_organization` | organization |
| `openai_base_url` | base URL |
| `openai_timeout_seconds` | request timeout in seconds |
| `openai_max_retries` | retries inside the OpenAI SDK |

```python
from doeff import run
from doeff_core_effects import lazy_ask, scheduled, state
from doeff_openai import openai_production_handler

env = {
    "openai_api_key": "sk-...",
    "openai_timeout_seconds": 30.0,
    "openai_max_retries": 3,
}
result = run(scheduled(lazy_ask(env=env)(state()(openai_production_handler(ai_workflow())))))
```

## Testing

Tests answer the effects with this package's mock handler instead of calling OpenAI:

```python
from doeff_openai import MockOpenAIConfig, MockOpenAIState, mock_handlers

config = MockOpenAIConfig(chat_responses=["hello"])
calls = MockOpenAIState()
program = mock_handlers(config=config, state=calls)(ai_workflow())
# run it like the production stack; calls.chat_calls counts the chat effects answered
```

## License

MIT License - see LICENSE file for details.
