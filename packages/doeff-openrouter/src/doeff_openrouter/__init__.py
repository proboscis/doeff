"""Public API for the :mod:`doeff_openrouter` package."""

from importlib import import_module
from typing import TYPE_CHECKING

from ._chat import OpenRouterResponseError
from ._client import OpenRouterClient
from ._effects import RouterChat, RouterStreamingChat, RouterStructuredOutput
from ._handlers import (
    mock_handlers,
    openrouter_mock_handler,
    openrouter_production_handler,
    production_handlers,
)
from ._structured_llm import structured_llm

__all__ = [
    "APICallMetadata",
    "CostInfo",
    "MockOpenRouterRuntime",
    "OpenRouterClient",
    "OpenRouterResponseError",
    "RouterChat",
    "RouterStreamingChat",
    "RouterStructuredOutput",
    "TokenUsage",
    "mock_handlers",
    "openrouter_mock_handler",
    "openrouter_production_handler",
    "production_handlers",
    "structured_llm",
]


# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers. A root that already has `__all__`
# lists the names there too; the tool does not add `__all__` to a root without one (DOEFF021).
if TYPE_CHECKING:
    from doeff_openrouter._handlers.testing import MockOpenRouterRuntime as MockOpenRouterRuntime
    from doeff_openrouter._types import APICallMetadata as APICallMetadata
    from doeff_openrouter._types import CostInfo as CostInfo
    from doeff_openrouter._types import TokenUsage as TokenUsage

_LAZY_EXPORTS: dict[str, str] = {
    "MockOpenRouterRuntime": "doeff_openrouter._handlers.testing",
    "APICallMetadata": "doeff_openrouter._types",
    "CostInfo": "doeff_openrouter._types",
    "TokenUsage": "doeff_openrouter._types",
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
