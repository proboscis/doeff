"""doeff-preset: Batteries-included handlers for doeff.

Provides opinionated, pre-configured handlers for common use cases,
keeping doeff core minimal while offering a convenient "just works"
experience for examples, demos, and rapid development.

Example:
    >>> from doeff import do, run_with_handler_map, slog
    >>> from doeff_preset import preset_handlers
    >>>
    >>> @do
    ... def my_workflow():
    ...     yield slog(step="start", msg="Beginning workflow")
    ...     # ... workflow logic
    ...     yield slog(step="done", msg="Workflow complete")
    ...     return "success"
    >>>
    >>> result = run_with_handler_map(my_workflow(), preset_handlers())
    >>> # slog messages are displayed to console AND accumulated in log
"""

from __future__ import annotations

from typing import Any

from doeff_preset.handlers.config import DEFAULT_CONFIG, config_handlers
from doeff_preset.handlers.log_display import log_display_handlers


def preset_handlers(
    config_defaults: dict[str, Any] | None = None,
) -> dict[type, Any]:
    """Return all preset handlers combined.

    Args:
        config_defaults: Optional dict to override default preset.* config values.

    Returns:
        Handler dict combining log display and config handlers.

    Example:
        >>> from doeff import run_with_handler_map
        >>> from doeff_preset import preset_handlers
        >>>
        >>> result = run_with_handler_map(my_workflow(), preset_handlers())
        >>>
        >>> # With custom config
        >>> custom = preset_handlers(config_defaults={"preset.log_level": "debug"})
        >>> result = run_with_handler_map(my_workflow(), custom)

    Handler Merge Pattern:
        Preset handlers can be merged with domain-specific handlers:

        >>> from doeff_preset import preset_handlers
        >>> from my_domain import domain_handlers
        >>>
        >>> # Domain handlers win on conflict
        >>> handlers = {**preset_handlers(), **domain_handlers()}
        >>>
        >>> # Preset handlers win on conflict
        >>> handlers = {**domain_handlers(), **preset_handlers()}
    """
    return {
        **log_display_handlers(),
        **config_handlers(config_defaults),
    }


__all__ = [
    "DEFAULT_CONFIG",
    "config_handlers",
    "log_display_handlers",
    "preset_handlers",
]
