"""Mock handlers for doeff-preset tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doeff import Delegate, WriterTellEffect
from doeff_preset.handlers.config import config_handlers

ProtocolHandler = Callable[[Any, Any], Any]


def _handle_tell_noop(effect: Any, _k):
    """Do not display structured logs during tests; delegate writer behavior."""
    if not isinstance(effect, WriterTellEffect):
        yield Delegate()
        return
    yield Delegate()


def mock_log_display_handlers() -> dict[type[Any], Any]:
    """Return no-op log display handlers for tests."""
    return {
        WriterTellEffect: _handle_tell_noop,
    }


def mock_handlers(config_defaults: dict[str, Any] | None = None) -> dict[type[Any], Any]:
    """Return test preset handlers (no-op display + configuration)."""
    return {
        **mock_log_display_handlers(),
        **config_handlers(config_defaults),
    }


__all__ = [
    "ProtocolHandler",
    "mock_handlers",
    "mock_log_display_handlers",
]
