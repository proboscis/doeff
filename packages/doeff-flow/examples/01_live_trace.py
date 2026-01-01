"""
Live Effect Trace Observability
===============================

This example demonstrates how to observe workflow execution in real-time
using doeff-flow's trace observer.

Run this example:
    cd packages/doeff-flow
    uv run python examples/01_live_trace.py

Then in another terminal, watch the trace:
    doeff-flow watch example-workflow --trace-dir .doeff-flow
"""

from pathlib import Path
from time import sleep

from doeff import do
from doeff.effects import Pure

# Import from doeff_flow
from doeff_flow import run_workflow, trace_observer


# =============================================================================
# Example 1: Using run_workflow convenience wrapper
# =============================================================================


@do
def fetch_data():
    """Simulate fetching data."""
    sleep(0.1)  # Simulate network latency
    return (yield Pure({"items": [1, 2, 3, 4, 5]}))


@do
def process_item(item: int):
    """Process a single item."""
    sleep(0.05)  # Simulate processing
    return (yield Pure(item * 2))


@do
def aggregate_results(results: list[int]):
    """Aggregate all results."""
    return (yield Pure(sum(results)))


@do
def main_workflow():
    """Main workflow that composes the above functions."""
    # Step 1: Fetch data
    data = yield fetch_data()
    print(f"Fetched data: {data}")

    # Step 2: Process each item
    results = []
    for item in data["items"]:
        result = yield process_item(item)
        results.append(result)
        print(f"Processed item {item} -> {result}")

    # Step 3: Aggregate
    total = yield aggregate_results(results)
    print(f"Total: {total}")

    return total


def example_run_workflow():
    """Run workflow with live trace using convenience wrapper."""
    print("=== Example 1: Using run_workflow ===")
    print("Run 'doeff-flow watch example-wf-001 --trace-dir .doeff-flow' in another terminal\n")

    result = run_workflow(
        main_workflow(),
        workflow_id="example-wf-001",
        trace_dir=Path(".doeff-flow"),
    )

    print(f"\nResult: {result.value}")
    print()


# =============================================================================
# Example 2: Using trace_observer with run_sync
# =============================================================================


def example_trace_observer():
    """Run workflow with trace_observer context manager."""
    from doeff.cesk import run_sync

    print("=== Example 2: Using trace_observer ===")
    print("Run 'doeff-flow watch example-wf-002 --trace-dir .doeff-flow' in another terminal\n")

    with trace_observer("example-wf-002", Path(".doeff-flow")) as on_step:
        result = run_sync(main_workflow(), on_step=on_step)

    print(f"\nResult: {result.value}")
    print()


# =============================================================================
# Example 3: Multiple concurrent workflows
# =============================================================================


@do
def workflow_a():
    """Workflow A - fast."""
    for i in range(3):
        yield Pure(f"A-{i}")
        sleep(0.1)
    return "A completed"


@do
def workflow_b():
    """Workflow B - slow."""
    for i in range(5):
        yield Pure(f"B-{i}")
        sleep(0.2)
    return "B completed"


def example_multiple_workflows():
    """Run multiple workflows and observe them."""
    import threading

    print("=== Example 3: Multiple Concurrent Workflows ===")
    print("Run 'doeff-flow ps --trace-dir .doeff-flow' in another terminal\n")

    def run_wf(wf, wf_id):
        run_workflow(wf(), workflow_id=wf_id, trace_dir=Path(".doeff-flow"))
        print(f"  {wf_id} finished")

    # Start both workflows in threads
    t1 = threading.Thread(target=run_wf, args=(workflow_a, "multi-wf-a"))
    t2 = threading.Thread(target=run_wf, args=(workflow_b, "multi-wf-b"))

    print("Starting workflows...")
    t1.start()
    t2.start()

    t1.join()
    t2.join()

    print("\nAll workflows completed!")
    print()


# =============================================================================
# Main
# =============================================================================


def main():
    """Run all examples."""
    example_run_workflow()
    example_trace_observer()
    example_multiple_workflows()

    print("=" * 60)
    print("Examples completed!")
    print()
    print("Try these commands to explore the traces:")
    print("  doeff-flow ps --trace-dir .doeff-flow")
    print("  doeff-flow history example-wf-001 --trace-dir .doeff-flow")
    print("  doeff-flow history example-wf-002 --last 20 --trace-dir .doeff-flow")


if __name__ == "__main__":
    main()
