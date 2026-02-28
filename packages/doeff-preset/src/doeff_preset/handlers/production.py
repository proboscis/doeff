"""Production handler composition for doeff-preset."""


from collections.abc import Callable
from typing import Any

from doeff import AskEffect, Effect, Pass, WriterTellEffect, do
from doeff_preset.handlers.config import config_handlers
from doeff_preset.handlers.log_display import log_display_handlers

ProtocolHandler = Callable[[Any, Any], Any]


def production_handlers(config_defaults: dict[str, Any] | None = None) -> ProtocolHandler:
    """Return the production preset protocol handler (display + configuration)."""
    slog_handler = log_display_handlers()
    ask_handler = config_handlers(config_defaults)

    @do
    def handler(effect: Effect, k: Any):
        if isinstance(effect, WriterTellEffect):
            return (yield slog_handler(effect, k))
        if isinstance(effect, AskEffect):
            return (yield ask_handler(effect, k))
        yield Pass()

    return handler


__all__ = [
    "ProtocolHandler",
    "production_handlers",
]
