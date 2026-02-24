"""Production handler composition for doeff-preset."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doeff import AskEffect, Pass, WriterTellEffect
from doeff_preset.handlers.config import config_handlers
from doeff_preset.handlers.log_display import log_display_handlers

ProtocolHandler = Callable[[Any, Any], Any]


def production_handlers(config_defaults: dict[str, Any] | None = None) -> ProtocolHandler:
    """Return the production preset protocol handler (display + configuration)."""
    slog_handler = log_display_handlers()
    ask_handler = config_handlers(config_defaults)

    def handler(effect: Any, k: Any):
        if isinstance(effect, WriterTellEffect):
            return (yield from slog_handler(effect, k))
        if isinstance(effect, AskEffect):
            return (yield from ask_handler(effect, k))
        yield Pass()

    return handler


__all__ = [
    "ProtocolHandler",
    "production_handlers",
]
