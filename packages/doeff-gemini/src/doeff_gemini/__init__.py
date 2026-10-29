"""doeff-gemini: Google Gemini integration with doeff effects."""

from importlib import import_module
from typing import TYPE_CHECKING

from doeff_image import ImageEdit, ImageGenerate, ImageResult

from ._client import GeminiClient
from ._costs import calculate_cost
from ._effects import (
    GeminiCalculateCost,
    GeminiChat,
    GeminiEmbedding,
    GeminiImageEdit,
    GeminiStreamingChat,
    GeminiStructuredOutput,
)
from ._handlers import (
    default_gemini_cost_handler,
    gemini_image_handler,
    gemini_mock_handler,
    gemini_production_handler,
    mock_handlers,
    production_handlers,
)
from ._structured_llm import (
    FileUriContentPart,
    GeminiContentPart,
    LocalFileContentPart,
    TextContentPart,
    UriContentPart,
    edit_image__gemini,
    structured_llm__gemini,
)
from ._types import (
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
    "FileUriContentPart",
    "GeminiCalculateCost",
    "GeminiCallResult",
    "GeminiChat",
    "GeminiClient",
    "GeminiContentPart",
    "GeminiCostEstimate",
    "GeminiEmbedding",
    "GeminiImageEdit",
    "GeminiImageEditResult",
    "GeminiStreamingChat",
    "GeminiStructuredOutput",
    "ImageEdit",
    "ImageGenerate",
    "ImageResult",
    "LocalFileContentPart",
    "MockGeminiHandler",
    "TextContentPart",
    "TokenUsage",
    "UriContentPart",
    "calculate_cost",
    "default_gemini_cost_handler",
    "edit_image__gemini",
    "gemini_image_handler",
    "gemini_mock_handler",
    "gemini_production_handler",
    "mock_handlers",
    "production_handlers",
    "structured_llm__gemini",
]


# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers; `__all__` is not used (DOEFF021).
if TYPE_CHECKING:
    from doeff_gemini._handlers.testing import MockGeminiHandler as MockGeminiHandler

_LAZY_EXPORTS: dict[str, str] = {
    "MockGeminiHandler": "doeff_gemini._handlers.testing",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
