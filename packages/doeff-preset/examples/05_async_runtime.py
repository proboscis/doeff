#!/usr/bin/env python
"""
Example 05: Async Runtime Support

Demonstrates that preset_handlers works with AsyncRuntime as well as SyncRuntime.

Run:
    cd packages/doeff-preset
    uv run python examples/05_async_runtime.py
"""

import asyncio

from doeff import Ask, AsyncRuntime, SyncRuntime, do
from doeff.effects.writer import slog
from doeff_preset import preset_handlers


@do
def async_workflow():
    """A workflow that works with both sync and async runtimes."""
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
    """Run the workflow with both runtimes."""
    handlers = preset_handlers()
    
    # Run with SyncRuntime
    print("=== Running with SyncRuntime ===\n")
    sync_runtime = SyncRuntime(handlers=handlers)
    sync_result = sync_runtime.run(async_workflow())
    print(f"\nSync result: {sync_result.value}")
    print(f"Log entries: {len(sync_result.log)}")
    
    # Run with AsyncRuntime
    print("\n=== Running with AsyncRuntime ===\n")
    async_runtime = AsyncRuntime(handlers=handlers)
    async_result = await async_runtime.run(async_workflow())
    print(f"\nAsync result: {async_result.value}")
    print(f"Log entries: {len(async_result.log)}")
    
    # Verify both produce same results
    assert sync_result.value == async_result.value
    assert len(sync_result.log) == len(async_result.log)
    print("\n✓ Both runtimes produce identical results!")


if __name__ == "__main__":
    asyncio.run(main())
