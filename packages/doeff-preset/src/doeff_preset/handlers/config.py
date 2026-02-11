"""Configuration handlers for preset.* Ask keys.

Provides default configuration values that can be queried via Ask("preset.*") effects.
"""

from __future__ import annotations

from typing import Any

from doeff import AskEffect, Delegate, MissingEnvKeyError, Resume
from doeff_preset.effects.config import is_preset_config_key

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

    def handle_ask_with_config(effect: Any, k):
        """Handle Ask effect with preset.* config support.

        - `preset.*` keys are resolved from this handler's config.
        - non-`preset.*` keys are delegated to the outer Reader handler.
        """
        if not isinstance(effect, AskEffect):
            yield Delegate()
            return

        key = effect.key

        if is_preset_config_key(key):
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
        >>> from doeff import Ask, do, run_with_handler_map
        >>> from doeff_preset import config_handlers
        >>>
        >>> @do
        ... def workflow():
        ...     show_logs = yield Ask("preset.show_logs")
        ...     return show_logs
        >>>
        >>> result = run_with_handler_map(workflow(), config_handlers())
        >>> # result.value == True (default)

        >>> # With custom defaults:
        >>> custom = config_handlers(defaults={"preset.show_logs": False})
        >>> result = run_with_handler_map(workflow(), custom)
        >>> # result.value == False
    """
    effect_type, handler = make_config_handler(defaults)
    return {effect_type: handler}


__all__ = [
    "DEFAULT_CONFIG",
    "config_handlers",
    "make_config_handler",
]
