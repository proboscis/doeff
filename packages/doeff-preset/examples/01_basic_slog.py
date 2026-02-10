#!/usr/bin/env python
"""
Example 01: Basic slog Display

Demonstrates how preset_handlers displays slog messages to the console
using rich formatting while also accumulating them in the writer log.

Run:
    cd packages/doeff-preset
    uv run python examples/01_basic_slog.py
"""

from doeff_preset import preset_handlers

from doeff import do, run_with_handler_map, slog


@do
def basic_workflow():
    """A simple workflow demonstrating slog display."""
    # slog messages are displayed to console AND accumulated in log
    yield slog(step="start", msg="Starting the workflow")

    yield slog(step="processing", status="running", item_count=42)

    # You can include any key-value pairs
    yield slog(
        step="validation",
        status="checking",
        checks_passed=True,
        duration_ms=150,
    )

    yield slog(level="warning", msg="This is a warning message")
    yield slog(level="error", msg="This is an error message")

    yield slog(step="done", msg="Workflow completed successfully")

    return "success"


def main():
    """Run the basic slog example."""
    print("=== Basic slog Display Example ===\n")

    result = run_with_handler_map(basic_workflow(), preset_handlers())

    print("\n=== Results ===")
    print(f"Return value: {result.value}")
    print(f"Log entries captured: {len(result.log)}")
    print("\nAccumulated log entries:")
    for i, entry in enumerate(result.log, 1):
        print(f"  {i}. {entry}")


if __name__ == "__main__":
    main()
