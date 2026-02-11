"""Production handler composition for doeff-preset."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doeff_preset.handlers.config import config_handlers
from doeff_preset.handlers.log_display import log_display_handlers

ProtocolHandler = Callable[[Any, Any], Any]


def production_handlers(config_defaults: dict[str, Any] | None = None) -> dict[type[Any], Any]:
    """Return production preset handlers (display + configuration)."""
    return {
        **log_display_handlers(),
        **config_handlers(config_defaults),
    }


__all__ = [
    "ProtocolHandler",
    "production_handlers",
]
