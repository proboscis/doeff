#!/usr/bin/env python
"""
Example 05: Async Runtime Support

Demonstrates that preset_handlers works with both run() and async_run().

Run:
    cd packages/doeff-preset
    uv run python examples/05_async_runtime.py
"""

import asyncio

from doeff_preset import preset_handlers

from doeff import Ask, do, slog
from doeff.rust_vm import async_run_with_handler_map, run_with_handler_map


@do
def async_workflow():
    """A workflow that works with both sync and async entrypoints."""
    yield slog(step="init", msg="Initializing workflow")

    # Query config
    show_logs = yield Ask("preset.show_logs")
    yield slog(step="config", show_logs=show_logs)

    # Simulate some work
    yield slog(step="processing", msg="Processing data...")
    yield slog(step="validating", msg="Validating results...")
    yield slog(step="complete", msg="Workflow finished")

    return {"status": "success", "logs_enabled": show_logs}


async def main():
    """Run the workflow with both sync and async APIs."""
    handlers = preset_handlers()

    # Run with run()
    print("=== Running with run() ===\n")
    sync_result = run_with_handler_map(async_workflow(), handlers)
    print(f"\nSync result: {sync_result.value}")
    print(f"Log entries: {len(sync_result.log)}")

    # Run with async_run()
    print("\n=== Running with async_run() ===\n")
    async_result = await async_run_with_handler_map(async_workflow(), handlers)
    print(f"\nAsync result: {async_result.value}")
    print(f"Log entries: {len(async_result.log)}")

    # Verify both produce same results
    assert sync_result.value == async_result.value
    assert len(sync_result.log) == len(async_result.log)
    print("\n✓ Both runtimes produce identical results!")


if __name__ == "__main__":
    asyncio.run(main())
