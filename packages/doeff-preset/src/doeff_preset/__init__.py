"""doeff-preset: Batteries-included handlers for doeff.

Provides opinionated, pre-configured handlers for common use cases,
keeping doeff core minimal while offering a convenient "just works"
experience for examples, demos, and rapid development.

Example:
    >>> from doeff import do, slog
    >>> from doeff import WithHandler, default_handlers, run
    >>> from doeff_preset import preset_handlers
    >>>
    >>> @do
    ... def my_workflow():
    ...     yield slog(step="start", msg="Beginning workflow")
    ...     # ... workflow logic
    ...     yield slog(step="done", msg="Workflow complete")
    ...     return "success"
    >>>
    >>> result = run(
    ...     WithHandler(preset_handlers(), my_workflow()),
    ...     handlers=default_handlers(),
    ... )
    >>> # slog messages are displayed to console AND accumulated in log
"""

from __future__ import annotations

from typing import Any

from doeff_preset.handlers import (
    DEFAULT_CONFIG,
    config_handlers,
    log_display_handlers,
    mock_handlers,
    production_handlers,
)


def preset_handlers(
    config_defaults: dict[str, Any] | None = None,
) -> Any:
    """Return the combined preset protocol handler.

    Args:
        config_defaults: Optional dict to override default preset.* config values.

    Returns:
        Protocol handler combining log display and config behavior.
    """
    return production_handlers(config_defaults=config_defaults)


__all__ = [
    "DEFAULT_CONFIG",
    "config_handlers",
    "log_display_handlers",
    "mock_handlers",
    "preset_handlers",
    "production_handlers",
]
