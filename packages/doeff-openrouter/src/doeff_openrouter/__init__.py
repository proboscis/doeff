"""Public API for the :mod:`doeff_openrouter` package."""

from .chat import OpenRouterResponseError, chat_completion
from .client import (
    OpenRouterClient,
    get_model_cost,
    get_openrouter_client,
    get_total_cost,
    reset_cost_tracking,
)
from .effects import RouterChat, RouterStreamingChat, RouterStructuredOutput
from .handlers import (
    mock_handlers,
    openrouter_mock_handler,
    openrouter_production_handler,
    production_handlers,
)
from .structured_llm import structured_llm

__all__ = [
    "OpenRouterClient",
    "OpenRouterResponseError",
    "RouterChat",
    "RouterStreamingChat",
    "RouterStructuredOutput",
    "chat_completion",
    "get_model_cost",
    "get_openrouter_client",
    "get_total_cost",
    "mock_handlers",
    "openrouter_mock_handler",
    "openrouter_production_handler",
    "production_handlers",
    "reset_cost_tracking",
    "structured_llm",
]
