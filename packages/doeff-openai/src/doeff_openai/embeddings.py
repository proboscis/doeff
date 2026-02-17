"""Embedding operations with comprehensive observability."""

import asyncio
import time
from typing import Any, Literal

from openai.types import CreateEmbeddingResponse

from doeff import (
    Await,
    EffectGenerator,
    Gather,
    Tell,
    Try,
    do,
)
from doeff_openai.client import get_openai_client, track_api_call
from doeff_openai.costs import (
    count_embedding_tokens,
)


@do
def create_embedding(
    input: str | list[str],
    model: str = "text-embedding-3-small",
    encoding_format: Literal["float", "base64"] | None = None,
    dimensions: int | None = None,
    user: str | None = None,
) -> EffectGenerator[CreateEmbeddingResponse]:
    """
    Create embeddings with full observability.

    Tracks:
    - Request/response in Graph with metadata
    - Token usage and costs
    - Latency
    - Errors
    """
    # Log the request
    input_count = len(input) if isinstance(input, list) else 1
    yield Tell(f"OpenAI embedding request: model={model}, inputs={input_count}")

    # Count input tokens
    input_tokens = count_embedding_tokens(input, model)
    yield Tell(f"Estimated input tokens: {input_tokens}")

    # Build request data
    request_data = {
        "input": input,
        "model": model,
    }

    # Add optional parameters
    if encoding_format is not None:
        request_data["encoding_format"] = encoding_format
    if dimensions is not None:
        request_data["dimensions"] = dimensions
    if user is not None:
        request_data["user"] = user

    # Get OpenAI client
    client = yield get_openai_client()

    # Define the main operation with retry support
    @do
    def make_api_call():
        # Track start time for this specific attempt
        attempt_start_time = time.time()

        # Define the API call with tracking
        @do
        def api_call_with_tracking():
            # Use Await effect for async API call
            response = yield Await(client.async_client.embeddings.create(**request_data))

            # Track successful API call
            metadata = yield track_api_call(
                operation="embedding",
                model=model,
                request_payload=request_data,
                response=response,
                start_time=attempt_start_time,
                error=None,
            )

            # Log embedding details
            yield Tell(
                f"Created {len(response.data)} embeddings, dimensions={len(response.data[0].embedding) if response.data else 0}"
            )

            return response

        # Use Try to track both success and failure
        safe_result = yield Try(api_call_with_tracking())
        if safe_result.is_err():
            # Track failed API call attempt (tracking will log the error)
            e = safe_result.error
            metadata = yield track_api_call(
                operation="embedding",
                model=model,
                request_payload=request_data,
                response=None,
                start_time=attempt_start_time,
                error=e,
            )
            # Re-raise to trigger retry
            raise e
        return safe_result.value

    # Retry logic for transient failures (3 attempts by default)
    max_attempts = 3
    delay_seconds = 1.0
    last_error = None
    for attempt in range(max_attempts):
        safe_result = yield Try(make_api_call())
        if safe_result.is_ok():
            result = safe_result.value
            break
        last_error = safe_result.error
        if attempt < max_attempts - 1:
            yield Tell(
                f"Embedding API call failed (attempt {attempt + 1}/{max_attempts}), retrying in {delay_seconds}s..."
            )
            yield Await(asyncio.sleep(delay_seconds))
    else:
        assert last_error is not None, "Should have an error if all retries failed"
        raise last_error
    return result


@do
def create_embedding_async(
    input: str | list[str],
    model: str = "text-embedding-3-small",
    **kwargs: Any,
) -> EffectGenerator[CreateEmbeddingResponse]:
    """
    Create embeddings using async client with full observability.
    """
    # Log the request
    input_count = len(input) if isinstance(input, list) else 1
    yield Tell(f"OpenAI async embedding request: model={model}, inputs={input_count}")

    # Build request data
    request_data = {
        "input": input,
        "model": model,
        **kwargs,
    }

    # Get OpenAI client
    client = yield get_openai_client()

    # Track start time
    start_time = time.time()

    # Define the main operation as a sub-program
    @do
    def main_operation():
        # Use Await effect for async API call
        async def create_embeddings():
            return await client.async_client.embeddings.create(**request_data)

        response = yield Await(create_embeddings())

        # Track the API call with full metadata
        metadata = yield track_api_call(
            operation="embedding",
            model=model,
            request_payload=request_data,
            response=response,
            start_time=start_time,
            error=None,
        )

        return response

    # Execute with Try to handle errors
    safe_result = yield Try(main_operation())
    if safe_result.is_err():
        # Track error
        e = safe_result.error
        metadata = yield track_api_call(
            operation="embedding",
            model=model,
            request_payload=request_data,
            response=None,
            start_time=start_time,
            error=e,
        )
        raise e
    return safe_result.value


@do
def batch_embeddings(
    texts: list[str],
    model: str = "text-embedding-3-small",
    batch_size: int = 100,
    **kwargs: Any,
) -> EffectGenerator[list[list[float]]]:
    """
    Create embeddings for a large list of texts in batches.

    Uses Gather effect to process batches in parallel while tracking each batch.
    """
    yield Tell(f"Batch embedding: {len(texts)} texts in batches of {batch_size}")

    # Split into batches
    batches = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        batches.append(batch)

    yield Tell(f"Processing {len(batches)} batches")

    # Process batches in parallel using Gather
    batch_responses = yield Gather(*[create_embedding(batch, model, **kwargs) for batch in batches])

    # Flatten the embeddings
    all_embeddings = []
    for response in batch_responses:
        for data in response.data:
            all_embeddings.append(data.embedding)

    yield Tell(f"Completed batch embedding: {len(all_embeddings)} embeddings created")

    return all_embeddings


@do
def get_single_embedding(
    text: str,
    model: str = "text-embedding-3-small",
    **kwargs: Any,
) -> EffectGenerator[list[float]]:
    """
    Get a single embedding vector for a text.

    Convenience function that returns just the embedding vector.
    """
    response = yield create_embedding(text, model, **kwargs)

    if response.data:
        return response.data[0].embedding

    return []


@do
def cosine_similarity(
    text1: str,
    text2: str,
    model: str = "text-embedding-3-small",
) -> EffectGenerator[float]:
    """
    Calculate cosine similarity between two texts using embeddings.

    Uses Gather to get both embeddings in parallel.
    """
    yield Tell(f"Calculating cosine similarity using {model}")

    # Get embeddings in parallel
    embeddings = yield Gather(
        *[
            get_single_embedding(text1, model),
            get_single_embedding(text2, model),
        ]
    )

    embedding1, embedding2 = embeddings

    # Calculate cosine similarity
    dot_product = sum(a * b for a, b in zip(embedding1, embedding2, strict=False))
    norm1 = sum(a * a for a in embedding1) ** 0.5
    norm2 = sum(b * b for b in embedding2) ** 0.5

    if norm1 == 0 or norm2 == 0:
        similarity = 0.0
    else:
        similarity = dot_product / (norm1 * norm2)

    yield Tell(f"Cosine similarity: {similarity:.4f}")

    yield Tell(f"Similarity tracking: model={model}, similarity={similarity:.4f}")

    return similarity


@do
def semantic_search(
    query: str,
    documents: list[str],
    model: str = "text-embedding-3-small",
    top_k: int = 5,
) -> EffectGenerator[list[tuple[int, float, str]]]:
    """
    Perform semantic search over documents.

    Returns top-k most similar documents with their indices and scores.
    """
    yield Tell(f"Semantic search: query over {len(documents)} documents")

    # Get query embedding
    query_embedding = yield get_single_embedding(query, model)

    # Get document embeddings in batches
    doc_embeddings = yield batch_embeddings(documents, model)

    # Calculate similarities
    similarities = []
    for i, doc_embedding in enumerate(doc_embeddings):
        # Calculate cosine similarity
        dot_product = sum(a * b for a, b in zip(query_embedding, doc_embedding, strict=False))
        norm1 = sum(a * a for a in query_embedding) ** 0.5
        norm2 = sum(b * b for b in doc_embedding) ** 0.5

        if norm1 > 0 and norm2 > 0:
            similarity = dot_product / (norm1 * norm2)
        else:
            similarity = 0.0

        similarities.append((i, similarity, documents[i]))

    # Sort by similarity and get top-k
    similarities.sort(key=lambda x: x[1], reverse=True)
    results = similarities[:top_k]

    yield Tell(
        f"Search complete: top {len(results)} results, best similarity={results[0][1]:.4f}"
        if results
        else "No results"
    )

    best_score = results[0][1] if results else 0.0
    yield Tell(
        f"Semantic search tracking: model={model}, documents={len(documents)}, "
        f"top_k={top_k}, best_score={best_score:.4f}"
    )

    return results
