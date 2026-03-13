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
    >>> from doeff import do, run
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
    >>> result = run(
    >>>     my_ai_workflow(),
    >>>     env={"openai_api_key": "sk-..."}
    >>> )
"""

from importlib import import_module
from typing import Any

__version__ = "0.1.0"

_EXPORTS_BY_MODULE = {
    "doeff_openai.chat": [
        "chat_completion",
        "chat_completion_async",
        "process_stream_chunks",
        "simple_chat",
    ],
    "doeff_openai.client": [
        "ClientHolder",
        "OpenAIClient",
        "extract_request_id",
        "extract_token_usage",
        "get_api_calls",
        "get_model_cost",
        "get_openai_client",
        "get_total_cost",
        "reset_cost_tracking",
        "track_api_call",
    ],
    "doeff_openai.costs": [
        "calculate_cost",
        "count_embedding_tokens",
        "count_message_tokens",
        "count_tokens",
        "estimate_cost",
        "estimate_max_cost",
        "get_encoding",
        "get_model_pricing",
    ],
    "doeff_openai.embeddings": [
        "batch_embeddings",
        "cosine_similarity",
        "create_embedding",
        "create_embedding_async",
        "get_single_embedding",
        "semantic_search",
    ],
    "doeff_openai.streaming": [
        "buffered_stream",
        "process_stream",
        "stream_to_chunks",
        "stream_with_accumulator",
        "stream_with_metadata",
    ],
    "doeff_openai.structured_llm": [
        "gpt4o_structured",
        "gpt5_nano_structured",
        "gpt5_structured",
        "is_gpt5_model",
        "requires_max_completion_tokens",
        "structured_llm__openai",
    ],
    "doeff_openai.types": [
        "MODEL_PRICING",
        "APICallMetadata",
        "CompletionRequest",
        "CostInfo",
        "EmbeddingRequest",
        "ModelPricing",
        "OpenAIModel",
        "StreamChunk",
        "TokenUsage",
    ],
}

_EXPORTS = {
    name: module_name
    for module_name, names in _EXPORTS_BY_MODULE.items()
    for name in names
}

__all__ = [
    "MODEL_PRICING",
    "APICallMetadata",
    "ClientHolder",
    "CompletionRequest",
    "CostInfo",
    "EmbeddingRequest",
    "ModelPricing",
    "OpenAIClient",
    "OpenAIModel",
    "StreamChunk",
    "TokenUsage",
    "__version__",
    "batch_embeddings",
    "buffered_stream",
    "calculate_cost",
    "chat_completion",
    "chat_completion_async",
    "cosine_similarity",
    "count_embedding_tokens",
    "count_message_tokens",
    "count_tokens",
    "create_embedding",
    "create_embedding_async",
    "estimate_cost",
    "estimate_max_cost",
    "extract_request_id",
    "extract_token_usage",
    "get_encoding",
    "get_api_calls",
    "get_model_cost",
    "get_model_pricing",
    "get_openai_client",
    "get_single_embedding",
    "get_total_cost",
    "gpt4o_structured",
    "gpt5_nano_structured",
    "gpt5_structured",
    "is_gpt5_model",
    "process_stream",
    "process_stream_chunks",
    "requires_max_completion_tokens",
    "reset_cost_tracking",
    "semantic_search",
    "simple_chat",
    "stream_to_chunks",
    "stream_with_accumulator",
    "stream_with_metadata",
    "structured_llm__openai",
    "track_api_call",
]


def __getattr__(name: str) -> Any:
    if name == "__version__":
        return __version__

    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
