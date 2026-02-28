"""Mock handlers for doeff-preset tests."""


from collections.abc import Callable
from typing import Any

from doeff import AskEffect, Effect, Pass, WriterTellEffect, do
from doeff_preset.handlers.config import config_handlers

ProtocolHandler = Callable[[Any, Any], Any]


@do
def _handle_tell_noop(effect: Effect, _k: Any):
    """Do not display structured logs during tests; delegate writer behavior."""
    if not isinstance(effect, WriterTellEffect):
        yield Pass()
        return None
    yield Pass()
    return None


def mock_log_display_handlers() -> ProtocolHandler:
    """Return a no-op log display protocol handler for tests."""
    return _handle_tell_noop


def mock_handlers(config_defaults: dict[str, Any] | None = None) -> ProtocolHandler:
    """Return the test preset protocol handler (no-op display + configuration)."""
    slog_handler = mock_log_display_handlers()
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
    "mock_handlers",
    "mock_log_display_handlers",
]
