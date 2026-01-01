"""
Durable Execution with Live Observability
==========================================

This example demonstrates durable workflow execution where:
- Expensive operations are cached to persistent storage (SQLite)
- On restart, cached results are retrieved (skipping expensive work)
- Progress is observable in real-time via doeff-flow

Run this example twice to see the difference:
    cd packages/doeff-flow

    # First run: All steps execute (takes ~5 seconds)
    uv run python examples/05_durable_execution.py

    # Second run: Cached steps are skipped (nearly instant)
    uv run python examples/05_durable_execution.py

Watch the execution:
    doeff-flow watch durable-demo --exit-on-complete

Reset and run again:
    uv run python examples/05_durable_execution.py --reset

Note: Traces are written to ~/.local/state/doeff-flow/ (XDG spec).
      Database is stored at ./durable_workflow.db
"""

import sys
import time
from pathlib import Path

from doeff import do
from doeff.effects.durable_cache import cacheget, cacheput
from doeff.storage import SQLiteStorage

from doeff_flow import run_workflow


# =============================================================================
# Simulated Expensive Operations
# =============================================================================


def expensive_api_call(endpoint: str) -> dict:
    """Simulate an expensive external API call."""
    print(f"  [API] Calling {endpoint}... (takes 1 second)")
    time.sleep(1.0)
    return {
        "endpoint": endpoint,
        "data": f"response_from_{endpoint}",
        "timestamp": time.time(),
    }


def expensive_computation(data: dict) -> dict:
    """Simulate expensive computation."""
    print(f"  [COMPUTE] Processing data... (takes 0.5 seconds)")
    time.sleep(0.5)
    return {
        "input": data,
        "result": f"computed_{data.get('data', 'unknown')}",
        "computed_at": time.time(),
    }


def expensive_aggregation(results: list[dict]) -> dict:
    """Simulate expensive aggregation."""
    print(f"  [AGGREGATE] Aggregating {len(results)} results... (takes 0.5 seconds)")
    time.sleep(0.5)
    return {
        "count": len(results),
        "items": [r.get("result", "unknown") for r in results],
        "aggregated_at": time.time(),
    }


# =============================================================================
# Durable Workflow Steps (with caching)
# =============================================================================


@do
def fetch_step(step_id: str, endpoint: str):
    """
    Fetch data from API with durable caching.

    If this step was previously completed and cached, the cached
    result is returned immediately (skipping the API call).
    """
    cache_key = f"fetch_{step_id}"

    # Check if we have a cached result
    cached = yield cacheget(cache_key)
    if cached is not None:
        print(f"  [CACHE HIT] Step '{step_id}' - using cached result")
        return cached

    print(f"  [CACHE MISS] Step '{step_id}' - executing...")

    # Execute the expensive operation
    result = expensive_api_call(endpoint)

    # Cache the result for future runs
    yield cacheput(cache_key, result)
    print(f"  [CACHED] Step '{step_id}' result saved")

    return result


@do
def compute_step(step_id: str, input_data: dict):
    """
    Compute result with durable caching.
    """
    cache_key = f"compute_{step_id}"

    cached = yield cacheget(cache_key)
    if cached is not None:
        print(f"  [CACHE HIT] Step '{step_id}' - using cached result")
        return cached

    print(f"  [CACHE MISS] Step '{step_id}' - executing...")
    result = expensive_computation(input_data)
    yield cacheput(cache_key, result)
    print(f"  [CACHED] Step '{step_id}' result saved")

    return result


@do
def aggregate_step(step_id: str, results: list[dict]):
    """
    Aggregate results with durable caching.
    """
    cache_key = f"aggregate_{step_id}"

    cached = yield cacheget(cache_key)
    if cached is not None:
        print(f"  [CACHE HIT] Step '{step_id}' - using cached result")
        return cached

    print(f"  [CACHE MISS] Step '{step_id}' - executing...")
    result = expensive_aggregation(results)
    yield cacheput(cache_key, result)
    print(f"  [CACHED] Step '{step_id}' result saved")

    return result


# =============================================================================
# Main Durable Workflow
# =============================================================================


@do
def durable_pipeline():
    """
    A durable data pipeline that survives restarts.

    Each step is cached to SQLite storage. If the workflow is interrupted
    and restarted, completed steps are skipped and execution resumes
    from where it left off.

    Total time on first run: ~5 seconds
    Total time on cached run: ~0 seconds
    """
    print("\n" + "=" * 60)
    print("Starting Durable Pipeline")
    print("=" * 60 + "\n")

    start_time = time.time()

    # Phase 1: Fetch data from multiple sources (parallel in real app)
    print("[Phase 1] Fetching data from sources...")

    source_a = yield fetch_step("source_a", "/api/users")
    source_b = yield fetch_step("source_b", "/api/orders")
    source_c = yield fetch_step("source_c", "/api/products")

    # Phase 2: Process each source
    print("\n[Phase 2] Processing data...")

    processed_a = yield compute_step("process_a", source_a)
    processed_b = yield compute_step("process_b", source_b)
    processed_c = yield compute_step("process_c", source_c)

    # Phase 3: Aggregate results
    print("\n[Phase 3] Aggregating results...")

    final_result = yield aggregate_step(
        "final_aggregation",
        [processed_a, processed_b, processed_c]
    )

    elapsed = time.time() - start_time

    print("\n" + "=" * 60)
    print(f"Pipeline Complete! (took {elapsed:.2f}s)")
    print("=" * 60 + "\n")

    return {
        "status": "success",
        "elapsed_seconds": round(elapsed, 2),
        "final_result": final_result,
    }


# =============================================================================
# Main
# =============================================================================


def main():
    # Parse --reset flag
    reset = "--reset" in sys.argv

    db_path = Path("durable_workflow.db")

    if reset and db_path.exists():
        print("Resetting: Deleting cached data...")
        db_path.unlink()
        print()

    print("=" * 60)
    print("Durable Execution Example")
    print("=" * 60)
    print()
    print("This workflow uses SQLite storage to cache expensive operations.")
    print("Run it twice to see how cached steps are skipped on the second run.")
    print()
    print("Watch command:")
    print("  doeff-flow watch durable-demo --exit-on-complete")
    print()
    print(f"Database: {db_path.absolute()}")
    print()

    if db_path.exists():
        print("[INFO] Found existing database - cached results will be used")
    else:
        print("[INFO] No existing database - all steps will execute")
    print()

    # Create SQLite storage for durable execution
    storage = SQLiteStorage(str(db_path))

    # Run the workflow with observability
    result = run_workflow(
        durable_pipeline(),
        workflow_id="durable-demo",
        storage=storage,
    )

    if result.is_ok:
        print(f"Final Result: {result.value}")
        print()
        print("Try running again to see cached execution!")
        print("Use --reset flag to clear the cache and start fresh.")
    else:
        print(f"Workflow failed: {result.error}")


if __name__ == "__main__":
    main()
