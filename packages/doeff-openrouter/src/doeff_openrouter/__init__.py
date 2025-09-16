"""Public API for the :mod:`doeff_openrouter` package."""

from .chat import OpenRouterResponseError, chat_completion
from .client import (
    OpenRouterClient,
    get_model_cost,
    get_openrouter_client,
    get_total_cost,
    reset_cost_tracking,
)
from .structured_llm import structured_llm

__all__ = [
    "OpenRouterClient",
    "OpenRouterResponseError",
    "chat_completion",
    "get_model_cost",
    "get_openrouter_client",
    "get_total_cost",
    "reset_cost_tracking",
    "structured_llm",
]
