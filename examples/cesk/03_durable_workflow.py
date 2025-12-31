"""
Durable Workflow Execution
==========================

This example demonstrates durable workflow execution using cacheget/cacheput
effects with SQLite storage. This pattern enables:

1. Surviving process restarts - cached results persist to disk
2. Idempotent operations - expensive operations only run once
3. Workflow resumption - continue from where you left off

The key pattern is: check cache -> if miss, compute and cache -> return result
"""

import os
import time
import tempfile
from pathlib import Path

from doeff import do
from doeff.cesk import run_sync
from doeff.effects import Pure, cacheget, cacheput, cachedelete, cacheexists
from doeff.storage import SQLiteStorage, InMemoryStorage


# =============================================================================
# Example 1: Basic cache operations
# =============================================================================


@do
def basic_cache_operations():
    """Demonstrate basic cacheget/cacheput operations."""
    # Check if value exists (should be None initially)
    initial = yield cacheget("my_key")

    # Store a value
    yield cacheput("my_key", {"data": 42, "timestamp": time.time()})

    # Retrieve the stored value
    stored = yield cacheget("my_key")

    # Check existence
    exists = yield cacheexists("my_key")

    return {
        "initial_value": initial,
        "stored_value": stored,
        "key_exists": exists,
    }


def example_basic_cache():
    """Show basic cache operations."""
    print("=== Example 1: Basic Cache Operations ===")

    # Use in-memory storage for this example
    storage = InMemoryStorage()
    result = run_sync(basic_cache_operations(), storage=storage)

    print(f"Initial value (before put): {result.value['initial_value']}")
    print(f"Stored value (after put): {result.value['stored_value']}")
    print(f"Key exists: {result.value['key_exists']}")
    print()


# =============================================================================
# Example 2: Idempotent expensive operation
# =============================================================================


def simulate_expensive_api_call(request_id: str) -> dict:
    """Simulate an expensive API call (e.g., external service, ML inference)."""
    print(f"  [API] Making expensive call for request: {request_id}")
    time.sleep(0.5)  # Simulate latency
    return {
        "request_id": request_id,
        "result": f"computed_result_{request_id}",
        "computed_at": time.time(),
    }


@do
def idempotent_operation(request_id: str):
    """
    Idempotent pattern: check cache before expensive operation.

    If result is cached, return immediately.
    Otherwise, compute, cache, and return.
    """
    cache_key = f"api_result:{request_id}"

    # Check cache first
    cached = yield cacheget(cache_key)
    if cached is not None:
        print(f"  [CACHE HIT] Returning cached result for {request_id}")
        return cached

    # Cache miss - perform expensive operation
    print(f"  [CACHE MISS] Computing for {request_id}")
    result = simulate_expensive_api_call(request_id)

    # Store in cache for future calls
    yield cacheput(cache_key, result)

    return result


@do
def workflow_with_caching():
    """Run multiple operations, demonstrating cache hits/misses."""
    # First call - cache miss
    result1 = yield idempotent_operation("req-001")

    # Second call same ID - cache hit
    result2 = yield idempotent_operation("req-001")

    # Different ID - cache miss
    result3 = yield idempotent_operation("req-002")

    # Same as first - cache hit
    result4 = yield idempotent_operation("req-001")

    return [result1, result2, result3, result4]


def example_idempotent():
    """Show idempotent operation pattern."""
    print("=== Example 2: Idempotent Operations ===")

    storage = InMemoryStorage()
    result = run_sync(workflow_with_caching(), storage=storage)

    print(f"\nTotal operations: {len(result.value)}")
    print(f"Unique computations: 2 (req-001 and req-002)")
    print()


# =============================================================================
# Example 3: Persistent workflow with SQLite
# =============================================================================


@do
def multi_step_workflow():
    """
    A workflow with multiple steps. Each step is cached.

    If the process crashes and restarts, completed steps
    will be retrieved from cache instead of re-executed.
    """
    results = []

    # Step 1: Data fetch
    step1 = yield cacheget("workflow:step1")
    if step1 is None:
        print("  Executing Step 1: Fetching data...")
        time.sleep(0.3)
        step1 = {"step": 1, "data": "raw_data", "timestamp": time.time()}
        yield cacheput("workflow:step1", step1)
    else:
        print("  Step 1: Using cached result")
    results.append(step1)

    # Step 2: Transform data
    step2 = yield cacheget("workflow:step2")
    if step2 is None:
        print("  Executing Step 2: Transforming data...")
        time.sleep(0.3)
        step2 = {"step": 2, "data": f"transformed_{step1['data']}", "timestamp": time.time()}
        yield cacheput("workflow:step2", step2)
    else:
        print("  Step 2: Using cached result")
    results.append(step2)

    # Step 3: Final processing
    step3 = yield cacheget("workflow:step3")
    if step3 is None:
        print("  Executing Step 3: Final processing...")
        time.sleep(0.3)
        step3 = {"step": 3, "data": f"final_{step2['data']}", "timestamp": time.time()}
        yield cacheput("workflow:step3", step3)
    else:
        print("  Step 3: Using cached result")
    results.append(step3)

    return results


def example_persistent_workflow():
    """Show workflow resumption with SQLite storage."""
    print("=== Example 3: Persistent Workflow with SQLite ===")

    # Create a temporary SQLite database
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "workflow.db"
        print(f"Using database: {db_path}")

        # First run - all steps execute
        print("\nFirst run (all steps execute):")
        storage = SQLiteStorage(db_path)
        result1 = run_sync(multi_step_workflow(), storage=storage)
        print(f"Completed {len(result1.value)} steps")

        # Simulate "process restart" - create new storage instance
        # pointing to the same database
        print("\nSecond run (simulating restart - all cached):")
        storage2 = SQLiteStorage(db_path)
        result2 = run_sync(multi_step_workflow(), storage=storage2)
        print(f"Completed {len(result2.value)} steps")

        # Show cache contents
        print("\nCache contents:")
        for key, value in storage2.items():
            print(f"  {key}: step={value['step']}")

    print()


# =============================================================================
# Example 4: Cache management (delete, clear)
# =============================================================================


@do
def cache_management_demo():
    """Demonstrate cache management operations."""
    # Populate cache
    yield cacheput("temp:a", "value_a")
    yield cacheput("temp:b", "value_b")
    yield cacheput("temp:c", "value_c")

    # Check existence
    exists_before = yield cacheexists("temp:b")

    # Delete specific key
    yield cachedelete("temp:b")

    # Check again
    exists_after = yield cacheexists("temp:b")
    value_after = yield cacheget("temp:b")

    # Other keys still exist
    a_value = yield cacheget("temp:a")
    c_value = yield cacheget("temp:c")

    return {
        "exists_before_delete": exists_before,
        "exists_after_delete": exists_after,
        "value_after_delete": value_after,
        "a_still_exists": a_value is not None,
        "c_still_exists": c_value is not None,
    }


def example_cache_management():
    """Show cache management operations."""
    print("=== Example 4: Cache Management ===")

    storage = InMemoryStorage()
    result = run_sync(cache_management_demo(), storage=storage)

    print(f"Key 'temp:b' exists before delete: {result.value['exists_before_delete']}")
    print(f"Key 'temp:b' exists after delete: {result.value['exists_after_delete']}")
    print(f"Value after delete: {result.value['value_after_delete']}")
    print(f"Other keys preserved: a={result.value['a_still_exists']}, c={result.value['c_still_exists']}")
    print()


# =============================================================================
# Example 5: Swappable storage backends
# =============================================================================


@do
def storage_agnostic_workflow():
    """A workflow that works with any storage backend."""
    yield cacheput("config:version", "1.0.0")
    yield cacheput("config:environment", "production")

    version = yield cacheget("config:version")
    env = yield cacheget("config:environment")

    return f"Running version {version} in {env}"


def example_swappable_backends():
    """Show how to swap storage backends."""
    print("=== Example 5: Swappable Storage Backends ===")

    # Same workflow, different backends
    print("\nUsing InMemoryStorage:")
    result1 = run_sync(storage_agnostic_workflow(), storage=InMemoryStorage())
    print(f"  Result: {result1.value}")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "config.db"
        print(f"\nUsing SQLiteStorage ({db_path}):")
        result2 = run_sync(storage_agnostic_workflow(), storage=SQLiteStorage(db_path))
        print(f"  Result: {result2.value}")

    print()


# =============================================================================
# Main
# =============================================================================


def main():
    """Run all examples."""
    example_basic_cache()
    example_idempotent()
    example_persistent_workflow()
    example_cache_management()
    example_swappable_backends()

    print("All durable workflow examples completed!")


if __name__ == "__main__":
    main()
