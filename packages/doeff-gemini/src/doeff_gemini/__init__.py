"""doeff-gemini: Google Gemini integration with doeff effects."""

__version__ = "0.1.0"

from .client import GeminiClient, get_gemini_client, track_api_call
from .costs import calculate_cost, gemini_cost_calculator__default
from .structured_llm import (
    build_contents,
    build_generation_config,
    process_image_edit_response,
    process_structured_response,
    process_unstructured_response,
    structured_llm__gemini,
    edit_image__gemini,
    image_edit__gemini,
)
from .types import (
    APICallMetadata,
    CostInfo,
    GeminiCallResult,
    GeminiCostEstimate,
    TokenUsage,
    GeminiImageEditResult,
)

__all__ = [
    "APICallMetadata",
    "CostInfo",
    "GeminiClient",
    "GeminiCallResult",
    "GeminiCostEstimate",
    "TokenUsage",
    "GeminiImageEditResult",
    "__version__",
    "build_contents",
    "build_generation_config",
    "get_gemini_client",
    "process_structured_response",
    "process_unstructured_response",
    "process_image_edit_response",
    "structured_llm__gemini",
    "edit_image__gemini",
    "image_edit__gemini",
    "track_api_call",
    "calculate_cost",
    "gemini_cost_calculator__default",
]
