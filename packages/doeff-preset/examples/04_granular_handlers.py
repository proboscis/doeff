#!/usr/bin/env python
"""
Example 04: Granular Handler Selection

Demonstrates how to use individual handler exports (log_display_handlers,
config_handlers) for fine-grained control.

Run:
    cd packages/doeff-preset
    uv run python examples/04_granular_handlers.py
"""

from doeff import Ask, SyncRuntime, do
from doeff.effects.writer import slog
from doeff_preset import config_handlers, log_display_handlers, preset_handlers


@do
def workflow_with_slog():
    """Workflow that only uses slog."""
    yield slog(step="start", msg="This workflow uses slog")
    yield slog(step="work", data="processing...")
    yield slog(step="done", msg="Complete")
    return "done"


@do  
def workflow_with_config():
    """Workflow that only uses config."""
    show_logs = yield Ask("preset.show_logs")
    log_level = yield Ask("preset.log_level")
    return {"show_logs": show_logs, "log_level": log_level}


def main():
    """Run granular handler examples."""
    # Example 1: Only slog display (no config)
    print("=== Only Log Display Handlers ===\n")
    runtime1 = SyncRuntime(handlers=log_display_handlers())
    result1 = runtime1.run(workflow_with_slog())
    print(f"Result: {result1.value}")
    print(f"Log entries: {len(result1.log)}")
    
    # Example 2: Only config handlers (slog won't display but will accumulate)
    # Note: Without log_display_handlers, slog still works but won't show rich output
    print("\n=== Only Config Handlers ===\n")
    runtime2 = SyncRuntime(handlers=config_handlers())
    result2 = runtime2.run(workflow_with_config())
    print(f"Config values: {result2.value}")
    
    # Example 3: Custom combination
    print("\n=== Custom Handler Combination ===\n")
    # Just slog display + custom config
    custom_config = config_handlers(defaults={
        "preset.show_logs": True,
        "preset.log_level": "debug",
        "preset.log_format": "simple",
    })
    handlers = {**log_display_handlers(), **custom_config}
    runtime3 = SyncRuntime(handlers=handlers)
    
    @do
    def combined_workflow():
        config = yield Ask("preset.log_level")
        yield slog(level=config, msg="Using custom log level")
        return config
    
    result3 = runtime3.run(combined_workflow())
    print(f"Log level used: {result3.value}")


if __name__ == "__main__":
    main()
