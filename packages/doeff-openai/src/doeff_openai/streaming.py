"""Streaming response handlers with observability."""

import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from openai.types.chat import ChatCompletionChunk

from doeff import (
    Await,
    EffectGenerator,
    Get,
    Put,
    Tell,
    Try,
    do,
)
from doeff_openai.costs import (
    calculate_cost,
    count_tokens,
)
from doeff_openai.types import (
    StreamChunk,
    TokenUsage,
)


@do
def process_stream(
    stream: AsyncIterator[ChatCompletionChunk],
    model: str,
    callback: Callable[[str], None] | None = None,
) -> EffectGenerator[tuple[str, TokenUsage, float]]:
    """
    Process a streaming response with full tracking.

    Args:
        stream: The async iterator of chunks from OpenAI
        model: The model being used
        callback: Optional callback for each chunk (e.g., for UI updates)

    Returns:
        Tuple of (full_content, token_usage, total_cost)
    """
    yield Tell(f"Starting stream processing for model={model}")

    # Initialize tracking
    full_content = ""
    total_chunks = 0
    start_time = time.time()
    role = None
    finish_reason = None

    # Accumulate chunks
    async def process_chunks():
        nonlocal full_content, total_chunks, role, finish_reason

        async for chunk in stream:
            total_chunks += 1

            if chunk.choices:
                choice = chunk.choices[0]

                # Extract role if present
                if choice.delta and choice.delta.role:
                    role = choice.delta.role

                # Extract content
                if choice.delta and choice.delta.content:
                    content = choice.delta.content
                    full_content += content

                    # Call callback if provided
                    if callback:
                        callback(content)

                # Extract finish reason
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

        return finish_reason

    # Process all chunks
    finish_reason = yield Await(process_chunks())

    # Log progress every 10 chunks
    if total_chunks > 0 and total_chunks % 10 == 0:
        yield Tell(f"Processed {total_chunks} chunks, content_length={len(full_content)}")

    # Calculate final metrics
    end_time = time.time()
    latency_ms = (end_time - start_time) * 1000

    # Count tokens
    output_tokens = count_tokens(full_content, model) if full_content else 0

    # Get input tokens from state (should be set by chat_completion)
    @do
    def _read_input_tokens():
        return (yield Get(f"stream_input_tokens_{model}"))

    safe_input_tokens = yield Try(_read_input_tokens())
    input_tokens = safe_input_tokens.value if safe_input_tokens.is_ok() else 0

    token_usage = TokenUsage(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )

    # Calculate cost
    cost_info = calculate_cost(model, token_usage)

    yield Tell(
        f"Stream metadata: model={model}, chunks={total_chunks}, total_tokens={token_usage.total_tokens}, "
        f"cost=${cost_info.total_cost:.6f}, latency_ms={latency_ms:.2f}, finish_reason={finish_reason}"
    )

    # Update cumulative costs
    @do
    def _read_total_cost():
        return (yield Get("total_openai_cost"))

    safe_total_cost = yield Try(_read_total_cost())
    current_total = safe_total_cost.value if safe_total_cost.is_ok() else 0.0
    yield Put("total_openai_cost", (current_total or 0.0) + cost_info.total_cost)

    yield Tell(
        f"Stream complete: chunks={total_chunks}, tokens={token_usage.total_tokens}, "
        f"cost=${cost_info.total_cost:.6f}, latency={latency_ms:.0f}ms"
    )

    return full_content, token_usage, cost_info.total_cost


@do
def stream_to_chunks(
    stream: AsyncIterator[ChatCompletionChunk],
    model: str,
) -> EffectGenerator[list[StreamChunk]]:
    """
    Convert a stream to a list of StreamChunk objects with tracking.
    """
    chunks = []

    async def collect():
        nonlocal chunks
        index = 0

        async for chunk in stream:
            if chunk.choices:
                choice = chunk.choices[0]

                stream_chunk = StreamChunk(
                    content=choice.delta.content if choice.delta else None,
                    role=choice.delta.role if choice.delta else None,
                    finish_reason=choice.finish_reason,
                    index=index,
                    model=model,
                )

                chunks.append(stream_chunk)
                index += 1

        return chunks

    chunks = yield Await(collect())

    yield Tell(f"Collected {len(chunks)} stream chunks")

    return chunks


@do
def stream_with_accumulator(
    stream: AsyncIterator[ChatCompletionChunk],
    model: str,
) -> EffectGenerator[AsyncIterator[tuple[str, str]]]:
    """
    Create a stream that yields (chunk_content, accumulated_content) pairs.

    Useful for UIs that want to show both the new chunk and the full text so far.
    """
    yield Tell(f"Creating accumulator stream for model={model}")

    async def accumulate():
        accumulated = ""

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta:
                content = chunk.choices[0].delta.content
                if content:
                    accumulated += content
                    yield content, accumulated

    return accumulate()


@do
def stream_with_metadata(
    stream: AsyncIterator[ChatCompletionChunk],
    model: str,
) -> EffectGenerator[AsyncIterator[dict[str, Any]]]:
    """
    Create a stream that yields chunks with full metadata.

    Each yielded item includes:
    - content: The new content
    - accumulated: The full content so far
    - tokens: Estimated token count so far
    - cost: Estimated cost so far
    - chunk_index: The chunk number
    """
    yield Tell(f"Creating metadata stream for model={model}")

    # Get input tokens from state
    @do
    def _read_input_tokens():
        return (yield Get(f"stream_input_tokens_{model}"))

    safe_input_tokens = yield Try(_read_input_tokens())
    input_tokens = safe_input_tokens.value if safe_input_tokens.is_ok() else 0

    async def with_metadata():
        accumulated = ""
        chunk_index = 0

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta:
                content = chunk.choices[0].delta.content
                if content:
                    accumulated += content
                    chunk_index += 1

                    # Estimate tokens
                    output_tokens = count_tokens(accumulated, model)
                    total_tokens = input_tokens + output_tokens

                    # Calculate cost
                    token_usage = TokenUsage(
                        prompt_tokens=input_tokens,
                        completion_tokens=output_tokens,
                        total_tokens=total_tokens,
                    )
                    cost_info = calculate_cost(model, token_usage)

                    yield {
                        "content": content,
                        "accumulated": accumulated,
                        "tokens": total_tokens,
                        "cost": cost_info.total_cost,
                        "chunk_index": chunk_index,
                        "finish_reason": chunk.choices[0].finish_reason,
                    }

    return with_metadata()


@do
def buffered_stream(
    stream: AsyncIterator[ChatCompletionChunk],
    model: str,
    buffer_size: int = 5,
    buffer_time_ms: int = 100,
) -> EffectGenerator[AsyncIterator[str]]:
    """
    Create a buffered stream that batches chunks for efficiency.

    Yields concatenated content from multiple chunks to reduce UI updates.
    """
    yield Tell(
        f"Creating buffered stream: buffer_size={buffer_size}, buffer_time_ms={buffer_time_ms}ms"
    )

    async def buffered():
        buffer = []
        last_yield_time = time.time()

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta:
                content = chunk.choices[0].delta.content
                if content:
                    buffer.append(content)

                    current_time = time.time()
                    time_since_yield = (current_time - last_yield_time) * 1000

                    # Yield if buffer is full or enough time has passed
                    if len(buffer) >= buffer_size or time_since_yield >= buffer_time_ms:
                        yield "".join(buffer)
                        buffer = []
                        last_yield_time = current_time

        # Yield any remaining content
        if buffer:
            yield "".join(buffer)

    return buffered()
