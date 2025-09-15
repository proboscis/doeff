"""
doeff-openai: OpenAI integration with comprehensive observability via Effects.

This package provides OpenAI API integration using the doeff effects system,
enabling full observability through Graph and Log effects for:
- Complete request/response tracking
- Token counting and cost calculation
- Latency measurement
- Error tracking
- Streaming support

Example:
    >>> from doeff import do, run_with_env
    >>> from doeff_openai import chat_completion, get_total_cost
    >>> 
    >>> @do
    >>> def my_ai_workflow():
    >>>     # API key provided via Reader environment
    >>>     response = yield chat_completion(
    >>>         messages=[{"role": "user", "content": "Hello!"}],
    >>>         model="gpt-3.5-turbo"
    >>>     )
    >>>     
    >>>     # Check accumulated cost
    >>>     total_cost = yield get_total_cost()
    >>>     print(f"Total cost: ${total_cost:.4f}")
    >>>     
    >>>     return response
    >>> 
    >>> # Run with API key in environment
    >>> result = run_with_env(
    >>>     my_ai_workflow(),
    >>>     env={"openai_api_key": "sk-..."}
    >>> )
"""

__version__ = "0.1.0"

# Client exports
from doeff_openai.client import (
    OpenAIClient,
    ClientHolder,
    get_openai_client,
    get_total_cost,
    get_model_cost,
    reset_cost_tracking,
    track_api_call,
    extract_token_usage,
    extract_request_id,
)

# Chat exports
from doeff_openai.chat import (
    chat_completion,
    chat_completion_async,
    process_stream_chunks,
    simple_chat,
)

# Embedding exports
from doeff_openai.embeddings import (
    create_embedding,
    create_embedding_async,
    batch_embeddings,
    get_single_embedding,
    cosine_similarity,
    semantic_search,
)

# Streaming exports
from doeff_openai.streaming import (
    process_stream,
    stream_to_chunks,
    stream_with_accumulator,
    stream_with_metadata,
    buffered_stream,
)

# Type exports
from doeff_openai.types import (
    # Enums
    OpenAIModel,
    
    # Data classes
    TokenUsage,
    CostInfo,
    APICallMetadata,
    ModelPricing,
    StreamChunk,
    CompletionRequest,
    EmbeddingRequest,
    
    # Constants
    MODEL_PRICING,
)

# Cost calculation exports
from doeff_openai.costs import (
    get_encoding,
    count_tokens,
    count_message_tokens,
    count_embedding_tokens,
    calculate_cost,
    estimate_cost,
    get_model_pricing,
    estimate_max_cost,
)

__all__ = [
    # Version
    "__version__",
    
    # Client
    "OpenAIClient",
    "ClientHolder",
    "get_openai_client",
    "get_total_cost",
    "get_model_cost",
    "reset_cost_tracking",
    "track_api_call",
    "extract_token_usage",
    "extract_request_id",
    
    # Chat
    "chat_completion",
    "chat_completion_async",
    "process_stream_chunks",
    "simple_chat",
    
    # Embeddings
    "create_embedding",
    "create_embedding_async",
    "batch_embeddings",
    "get_single_embedding",
    "cosine_similarity",
    "semantic_search",
    
    # Streaming
    "process_stream",
    "stream_to_chunks",
    "stream_with_accumulator",
    "stream_with_metadata",
    "buffered_stream",
    
    # Types
    "OpenAIModel",
    "TokenUsage",
    "CostInfo",
    "APICallMetadata",
    "ModelPricing",
    "StreamChunk",
    "CompletionRequest",
    "EmbeddingRequest",
    "MODEL_PRICING",
    
    # Cost calculation
    "get_encoding",
    "count_tokens",
    "count_message_tokens",
    "count_embedding_tokens",
    "calculate_cost",
    "estimate_cost",
    "get_model_pricing",
    "estimate_max_cost",
]