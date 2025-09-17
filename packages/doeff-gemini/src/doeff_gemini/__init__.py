"""doeff-gemini: Google Gemini integration with doeff effects."""

__version__ = "0.1.0"

from .client import GeminiClient, get_gemini_client, track_api_call
from .costs import calculate_cost
from .structured_llm import (
    build_contents,
    build_generation_config,
    process_structured_response,
    process_unstructured_response,
    structured_llm__gemini,
)
from .types import APICallMetadata, CostInfo, TokenUsage

__all__ = [
    "APICallMetadata",
    "CostInfo",
    "GeminiClient",
    "TokenUsage",
    "__version__",
    "build_contents",
    "build_generation_config",
    "get_gemini_client",
    "process_structured_response",
    "process_unstructured_response",
    "structured_llm__gemini",
    "track_api_call",
    "calculate_cost",
]
