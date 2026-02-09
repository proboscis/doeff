"""
Live Effect Trace Observability
===============================

This example demonstrates how to observe workflow execution in real-time
using doeff-flow's trace observer.

Run this example:
    cd packages/doeff-flow
    uv run python examples/01_live_trace.py

Then in another terminal, watch the trace:
    doeff-flow watch example-wf-001

Note: By default, traces are written to ~/.local/state/doeff-flow/ (XDG spec).
You can override with --trace-dir or DOEFF_FLOW_TRACE_DIR env var.
"""

from time import sleep

# Import from doeff_flow
from doeff_flow import run_workflow, trace_observer

from doeff import default_handlers, do, run as run_sync
from doeff.effects.writer import slog

# =============================================================================
# Example 1: Using run_workflow convenience wrapper
# =============================================================================


@do
def fetch_data():
    """Simulate fetching data."""
    sleep(0.1)  # Simulate network latency
    return {"items": [1, 2, 3, 4, 5]}


@do
def process_item(item: int):
    """Process a single item."""
    sleep(0.05)  # Simulate processing
    return item * 2


@do
def aggregate_results(results: list[int]):
    """Aggregate all results."""
    return sum(results)


@do
def main_workflow():
    """Main workflow that composes the above functions."""
    # Step 1: Fetch data
    data = yield fetch_data()
    yield slog(step="fetch", msg=f"Fetched data: {data}")

    # Step 2: Process each item
    results = []
    for item in data["items"]:
        result = yield process_item(item)
        results.append(result)
        yield slog(step="process", item=item, result=result)

    # Step 3: Aggregate
    total = yield aggregate_results(results)
    yield slog(step="aggregate", total=total)

    return total


def example_run_workflow():
    """Run workflow with live trace using convenience wrapper."""
    print("=== Example 1: Using run_workflow ===")
    print("Run 'doeff-flow watch example-wf-001' in another terminal\n")

    # Uses XDG default trace directory (~/.local/state/doeff-flow)
    result = run_workflow(
        main_workflow(),
        workflow_id="example-wf-001",
    )

    print(f"\nResult: {result.value}")
    print()


# =============================================================================
# Example 2: Using trace_observer with run_sync
# =============================================================================


def example_trace_observer():
    """Run workflow with trace_observer context manager."""
    from doeff_flow.trace import write_terminal_trace

    print("=== Example 2: Using trace_observer ===")
    print("Run 'doeff-flow watch example-wf-002' in another terminal\n")

    # Uses XDG default trace directory
    with trace_observer("example-wf-002") as on_step:
        _ = on_step
        result = run_sync(main_workflow(), handlers=default_handlers())
        write_terminal_trace("example-wf-002", None, result)

    print(f"\nResult: {result.value}")
    print()


# =============================================================================
# Example 3: Multiple concurrent workflows
# =============================================================================


@do
def workflow_a():
    """Workflow A - fast."""
    for i in range(3):
        # Just yield sub-workflows to generate trace entries
        yield process_item(i)
        sleep(0.1)
    return "A completed"


@do
def workflow_b():
    """Workflow B - slow."""
    for i in range(5):
        yield process_item(i)
        sleep(0.2)
    return "B completed"


def run_traced_workflow(workflow_factory, workflow_id: str):
    """Run one workflow and return its ID."""
    result = run_workflow(workflow_factory(), workflow_id=workflow_id)
    if not result.is_ok:
        raise RuntimeError(f"Workflow {workflow_id} failed: {result.error}")
    return workflow_id


def example_multiple_workflows():
    """Run multiple workflows and observe them."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    print("=== Example 3: Multiple Concurrent Workflows ===")
    print("Run 'doeff-flow watch' or 'doeff-flow ps' in another terminal\n")

    print("Starting workflows...")

    completed_workflows = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(run_traced_workflow, workflow_a, "multi-wf-a"),
            executor.submit(run_traced_workflow, workflow_b, "multi-wf-b"),
        ]
        for future in as_completed(futures):
            completed_workflows.append(future.result())

    for workflow_id in sorted(completed_workflows):
        print(f"  {workflow_id} finished")
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
    print("  doeff-flow ps")
    print("  doeff-flow watch")
    print("  doeff-flow history example-wf-001")
    print("  doeff-flow history example-wf-002 --last 20")


if __name__ == "__main__":
    main()
