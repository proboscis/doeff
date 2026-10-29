"""
doeff-openai: OpenAI integration with comprehensive observability via Effects.

This package provides OpenAI API integration using the doeff effects system,
enabling full observability through Graph and Log effects for:
- Complete request/response tracking
- Token counting and cost calculation
- Latency measurement
- Error tracking
- Streaming support

The public API is the effects (``ChatCompletion``, ``Embedding``, ``StructuredOutput`` and the
provider-agnostic effects of doeff-llm they extend) and the handlers that answer them
(``production_handlers``, ``openai_production_handler``, ``mock_handlers``). The program helpers
the handlers are built from (``chat_completion``, ``create_embedding``, ``get_total_cost`` ...)
live in the modules behind ``_`` and are not part of it (ADR-DOE-API-001). So do the OpenAI client
class, the pricing table and the token counters: the handlers build the client from the Reader
keys ``openai_api_key``, ``openai_organization``, ``openai_base_url``, ``openai_timeout_seconds`` and
``openai_max_retries``, and price a call through the ``CalculateCost`` effect.

Example:
    >>> from doeff import do
    >>> from doeff_llm import LLMChat
    >>>
    >>> @do
    >>> def my_ai_workflow():
    >>>     # Answered by production_handlers; API key provided via Reader environment
    >>>     response = yield LLMChat(
    >>>         messages=[{"role": "user", "content": "Hello!"}],
    >>>         model="gpt-3.5-turbo"
    >>>     )
    >>>     return response
"""

from importlib import import_module
from typing import TYPE_CHECKING

# Exception types the cost handler raises (ADR-DOE-API-001 R2)
from doeff_openai._costs import (
    MissingCachedPricingError,
    PricingError,
    UnknownModelPricingError,
)

# Effect exports (includes CalculateCost)
from doeff_openai._effects import (
    CalculateCost,
    ChatCompletion,
    Embedding,
    StreamingChatCompletion,
    StructuredOutput,
)

# Structured LLM exports
from doeff_openai._structured_llm import structured_llm__openai

# Type exports
from doeff_openai._types import (
    APICallMetadata,
    CompletionRequest,
    CostInfo,
    EmbeddingRequest,
    # Enums
    OpenAIModel,
    StreamChunk,
    # Data classes
    TokenUsage,
)

__all__ = [
    "APICallMetadata",
    "CalculateCost",
    "ChatCompletion",
    "CompletionRequest",
    "CostInfo",
    "Embedding",
    "EmbeddingRequest",
    "MissingCachedPricingError",
    "MockOpenAIConfig",
    "MockOpenAIState",
    # Types
    "OpenAIModel",
    "PricingError",
    "StreamChunk",
    "StreamingChatCompletion",
    "StructuredOutput",
    "TokenUsage",
    "UnknownModelPricingError",
    "calculate_cost_handler",
    "mock_handlers",
    "openai_mock_handler",
    "openai_production_handler",
    "production_handlers",
    # Structured LLM
    "structured_llm__openai",
]


# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers. A root that already has `__all__`
# lists the names there too; the tool does not add `__all__` to a root without one (DOEFF021).
if TYPE_CHECKING:
    from doeff_openai._handlers.production import calculate_cost_handler as calculate_cost_handler
    from doeff_openai._handlers.production import (
        openai_production_handler as openai_production_handler,
    )
    from doeff_openai._handlers.production import production_handlers as production_handlers
    from doeff_openai._handlers.testing import MockOpenAIConfig as MockOpenAIConfig
    from doeff_openai._handlers.testing import MockOpenAIState as MockOpenAIState
    from doeff_openai._handlers.testing import mock_handlers as mock_handlers
    from doeff_openai._handlers.testing import openai_mock_handler as openai_mock_handler

_LAZY_EXPORTS: dict[str, str] = {
    "MockOpenAIConfig": "doeff_openai._handlers.testing",
    "MockOpenAIState": "doeff_openai._handlers.testing",
    "calculate_cost_handler": "doeff_openai._handlers.production",
    "mock_handlers": "doeff_openai._handlers.testing",
    "openai_mock_handler": "doeff_openai._handlers.testing",
    "openai_production_handler": "doeff_openai._handlers.production",
    "production_handlers": "doeff_openai._handlers.production",
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
