"""doeff-gemini: Google Gemini integration with doeff effects."""

__version__ = "0.1.0"

from .client import GeminiClient, get_gemini_client, track_api_call
from .costs import calculate_cost, gemini_cost_calculator__default
from .structured_llm import (
    build_contents,
    build_generation_config,
    edit_image__gemini,
    image_edit__gemini,
    process_image_edit_response,
    process_structured_response,
    process_unstructured_response,
    structured_llm__gemini,
)
from .types import (
    APICallMetadata,
    CostInfo,
    GeminiCallResult,
    GeminiCostEstimate,
    GeminiImageEditResult,
    TokenUsage,
)

__all__ = [
    "APICallMetadata",
    "CostInfo",
    "GeminiCallResult",
    "GeminiClient",
    "GeminiCostEstimate",
    "GeminiImageEditResult",
    "TokenUsage",
    "__version__",
    "build_contents",
    "build_generation_config",
    "calculate_cost",
    "edit_image__gemini",
    "gemini_cost_calculator__default",
    "get_gemini_client",
    "image_edit__gemini",
    "process_image_edit_response",
    "process_structured_response",
    "process_unstructured_response",
    "structured_llm__gemini",
    "track_api_call",
]
