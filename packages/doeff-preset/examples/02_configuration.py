#!/usr/bin/env python
"""
Example 02: Configuration via Ask

Demonstrates how to query preset configuration values using Ask("preset.*")
and how to override defaults.

Run:
    cd packages/doeff-preset
    uv run python examples/02_configuration.py
"""

from doeff_preset import preset_handlers

from doeff import Ask, WithHandler, default_handlers, do, run, slog


@do
def configurable_workflow():
    """A workflow that queries configuration via Ask."""
    # Query preset configuration
    show_logs = yield Ask("preset.show_logs")
    log_level = yield Ask("preset.log_level")
    log_format = yield Ask("preset.log_format")

    yield slog(
        step="config",
        msg="Configuration loaded",
        show_logs=show_logs,
        log_level=log_level,
        log_format=log_format,
    )

    # Conditional behavior based on config
    if show_logs:
        yield slog(step="info", msg="Logs are enabled")

    return {
        "show_logs": show_logs,
        "log_level": log_level,
        "log_format": log_format,
    }


def main():
    """Run configuration examples."""
    # Example 1: Default configuration
    print("=== Default Configuration ===\n")
    result = run(
        WithHandler(preset_handlers(), configurable_workflow()),
        handlers=default_handlers(),
    )
    print(f"\nConfig: {result.value}")

    # Example 2: Custom configuration
    print("\n=== Custom Configuration ===\n")
    custom_handlers = preset_handlers(
        config_defaults={
            "preset.show_logs": False,
            "preset.log_level": "debug",
            "preset.log_format": "json",
        }
    )
    result2 = run(
        WithHandler(custom_handlers, configurable_workflow()),
        handlers=default_handlers(),
    )
    print(f"\nConfig: {result2.value}")


if __name__ == "__main__":
    main()
