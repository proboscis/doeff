"""Configuration handlers for preset.* Ask keys.

Provides default configuration values that can be queried via Ask("preset.*") effects.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doeff import AskEffect, Effect, MissingEnvKeyError, Pass, Resume, do
from doeff_preset.effects.config import is_preset_config_key

ProtocolHandler = Callable[[Any, Any], Any]

# Default preset configuration
DEFAULT_CONFIG: dict[str, Any] = {
    "preset.show_logs": True,
    "preset.log_level": "info",
    "preset.log_format": "rich",  # "simple" | "rich" | "json"
}


def make_config_handler(
    defaults: dict[str, Any] | None = None,
) -> ProtocolHandler:
    """Create a config handler for Ask("preset.*") effects.

    This is a DECORATOR handler that wraps the default Ask handler.
    It intercepts Ask effects with "preset.*" keys and returns values
    from the defaults dict.

    For non-preset keys, it falls back to the normal Ask behavior
    (looking up in env).

    Args:
        defaults: Optional dict of preset.* -> value mappings.
                  Merged with DEFAULT_CONFIG (user values override).

    Returns:
        Protocol handler for use with ``WithHandler``.
    """
    config = {**DEFAULT_CONFIG}
    if defaults:
        config.update(defaults)

    @do
    def handle_ask_with_config(effect: Effect, k: Any):
        """Handle Ask effect with preset.* config support.

        - `preset.*` keys are resolved from this handler's config.
        - non-`preset.*` keys are delegated to the outer Reader handler.
        """
        if not isinstance(effect, AskEffect):
            yield Pass()
            return None

        key = effect.key

        if is_preset_config_key(key):
            if key not in config:
                raise MissingEnvKeyError(key)
            return (yield Resume(k, config[key]))

        yield Pass()

    return handle_ask_with_config


def config_handlers(defaults: dict[str, Any] | None = None) -> ProtocolHandler:
    """Return a protocol handler for preset configuration.

    Args:
        defaults: Optional dict to override default config values.

    Returns:
        Protocol handler that supports ``preset.*`` Ask keys.
    """
    return make_config_handler(defaults)


__all__ = [
    "DEFAULT_CONFIG",
    "ProtocolHandler",
    "config_handlers",
    "make_config_handler",
]
