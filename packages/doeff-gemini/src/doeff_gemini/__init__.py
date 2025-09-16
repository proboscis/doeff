"""doeff-gemini: Google Gemini integration with doeff effects."""

__version__ = "0.1.0"

from .client import GeminiClient, get_gemini_client, track_api_call
from .structured_llm import (
    structured_llm__gemini,
    build_contents,
    build_generation_config,
    process_structured_response,
    process_unstructured_response,
)
from .types import APICallMetadata, TokenUsage

__all__ = [
    "__version__",
    "GeminiClient",
    "get_gemini_client",
    "track_api_call",
    "structured_llm__gemini",
    "build_contents",
    "build_generation_config",
    "process_structured_response",
    "process_unstructured_response",
    "APICallMetadata",
    "TokenUsage",
]
