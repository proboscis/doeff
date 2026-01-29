"""
Data Pipeline with Live Observability
======================================

This example shows a realistic data processing pipeline where you can
observe each stage of execution in real-time.

Run this example:
    cd packages/doeff-flow
    uv run python examples/02_data_pipeline.py

In another terminal, watch the execution:
    doeff-flow watch data-pipeline --exit-on-complete

Note: By default, traces are written to ~/.local/state/doeff-flow/ (XDG spec).
"""

import random
import time

from doeff_flow import run_workflow

from doeff import do

# =============================================================================
# Data Pipeline Stages
# =============================================================================


@do
def extract_data(source: str):
    """Extract data from a source (simulated)."""
    print(f"  [Extract] Reading from {source}...")
    time.sleep(0.2)  # Simulate I/O

    # Simulate extracted records
    records = [
        {"id": i, "value": random.randint(1, 100), "source": source}
        for i in range(5)
    ]

    print(f"  [Extract] Got {len(records)} records")
    return records


@do
def transform_record(record: dict):
    """Transform a single record."""
    time.sleep(0.05)  # Simulate processing

    return {
        "id": record["id"],
        "original_value": record["value"],
        "doubled": record["value"] * 2,
        "source": record["source"],
        "processed": True,
    }


@do
def transform_all(records: list[dict]):
    """Transform all records."""
    print(f"  [Transform] Processing {len(records)} records...")
    transformed = []

    for i, record in enumerate(records):
        result = yield transform_record(record)
        transformed.append(result)
        print(f"  [Transform] Processed record {i + 1}/{len(records)}")

    return transformed


@do
def load_data(records: list[dict], destination: str):
    """Load transformed data to destination (simulated)."""
    print(f"  [Load] Writing {len(records)} records to {destination}...")
    time.sleep(0.1)  # Simulate I/O

    # Simulate loading
    loaded_count = len(records)
    print(f"  [Load] Successfully loaded {loaded_count} records")
    return loaded_count


@do
def aggregate_stats(records: list[dict]):
    """Calculate aggregate statistics."""
    print("  [Aggregate] Computing statistics...")

    total = sum(r["doubled"] for r in records)
    avg = total / len(records) if records else 0
    max_val = max(r["doubled"] for r in records) if records else 0
    min_val = min(r["doubled"] for r in records) if records else 0

    return {
        "count": len(records),
        "total": total,
        "average": round(avg, 2),
        "max": max_val,
        "min": min_val,
    }


# =============================================================================
# Main Pipeline
# =============================================================================


@do
def etl_pipeline(source: str, destination: str):
    """
    Complete ETL pipeline with observability.

    Stages:
    1. Extract - Read data from source
    2. Transform - Process each record
    3. Load - Write to destination
    4. Aggregate - Calculate statistics
    """
    print(f"\n{'=' * 60}")
    print("Starting ETL Pipeline")
    print(f"  Source: {source}")
    print(f"  Destination: {destination}")
    print(f"{'=' * 60}\n")

    # Stage 1: Extract
    raw_data = yield extract_data(source)

    # Stage 2: Transform
    transformed_data = yield transform_all(raw_data)

    # Stage 3: Load
    loaded_count = yield load_data(transformed_data, destination)

    # Stage 4: Aggregate
    stats = yield aggregate_stats(transformed_data)

    print(f"\n{'=' * 60}")
    print("Pipeline Complete!")
    print(f"  Records processed: {loaded_count}")
    print(f"  Statistics: {stats}")
    print(f"{'=' * 60}\n")

    return {
        "loaded_count": loaded_count,
        "stats": stats,
    }


# =============================================================================
# Main
# =============================================================================


def main():
    print("=" * 60)
    print("Data Pipeline Example with Live Observability")
    print("=" * 60)
    print()
    print("Run this command in another terminal to watch:")
    print("  doeff-flow watch data-pipeline --exit-on-complete")
    print()
    input("Press Enter to start the pipeline...")

    result = run_workflow(
        etl_pipeline(
            source="database://production/users",
            destination="warehouse://analytics/users_processed",
        ),
        workflow_id="data-pipeline",
    )

    if result.is_ok:
        print(f"\nFinal Result: {result.value}")
    else:
        print(f"\nPipeline failed: {result.error}")


if __name__ == "__main__":
    main()
