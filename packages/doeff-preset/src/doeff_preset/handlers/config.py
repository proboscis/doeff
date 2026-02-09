"""Configuration handlers for preset.* Ask keys.

Provides default configuration values that can be queried via Ask("preset.*") effects.
"""

from __future__ import annotations

from typing import Any

from doeff import Delegate, Resume
from doeff.errors import MissingEnvKeyError
from doeff.effects.reader import AskEffect


# Default preset configuration
DEFAULT_CONFIG: dict[str, Any] = {
    "preset.show_logs": True,
    "preset.log_level": "info",
    "preset.log_format": "rich",  # "simple" | "rich" | "json"
}


def make_config_handler(
    defaults: dict[str, Any] | None = None,
) -> tuple[type, Any]:
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
        Tuple of (AskEffect, handler) for use in handler dict.
    """
    config = {**DEFAULT_CONFIG}
    if defaults:
        config.update(defaults)

    def handle_ask_with_config(effect: AskEffect, k):
        """Handle Ask effect with preset.* config support.

        - `preset.*` keys are resolved from this handler's config.
        - non-`preset.*` keys are delegated to the outer Reader handler.
        """
        key = effect.key

        if isinstance(key, str) and key.startswith("preset."):
            if key not in config:
                raise MissingEnvKeyError(key)
            return (yield Resume(k, config[key]))

        yield Delegate()

    return AskEffect, handle_ask_with_config


def config_handlers(defaults: dict[str, Any] | None = None) -> dict[type, Any]:
    """Return handlers for preset configuration.

    Args:
        defaults: Optional dict to override default config values.

    Returns:
        Handler dict with AskEffect handler that supports preset.* keys.

    Example:
        >>> from doeff import SyncRuntime, do, Ask
        >>> from doeff_preset import config_handlers
        >>>
        >>> @do
        ... def workflow():
        ...     show_logs = yield Ask("preset.show_logs")
        ...     return show_logs
        >>>
        >>> runtime = SyncRuntime(handlers=config_handlers())
        >>> result = runtime.run(workflow())
        >>> # result.value == True (default)

        >>> # With custom defaults:
        >>> custom = config_handlers(defaults={"preset.show_logs": False})
        >>> runtime = SyncRuntime(handlers=custom)
        >>> result = runtime.run(workflow())
        >>> # result.value == False
    """
    effect_type, handler = make_config_handler(defaults)
    return {effect_type: handler}


__all__ = [
    "DEFAULT_CONFIG",
    "config_handlers",
    "make_config_handler",
]
