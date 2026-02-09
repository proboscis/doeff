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

from doeff import do
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
    from doeff import run as run_sync
    from doeff_flow.trace import write_terminal_trace

    print("=== Example 2: Using trace_observer ===")
    print("Run 'doeff-flow watch example-wf-002' in another terminal\n")

    # Uses XDG default trace directory
    with trace_observer("example-wf-002") as on_step:
        _ = on_step
        result = run_sync(main_workflow())
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


def example_multiple_workflows():
    """Run multiple workflows and observe them."""
    import threading

    print("=== Example 3: Multiple Concurrent Workflows ===")
    print("Run 'doeff-flow watch' or 'doeff-flow ps' in another terminal\n")

    def run_wf(wf, wf_id):
        # Uses XDG default trace directory
        run_workflow(wf(), workflow_id=wf_id)
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
    print("  doeff-flow ps")
    print("  doeff-flow watch")
    print("  doeff-flow history example-wf-001")
    print("  doeff-flow history example-wf-002 --last 20")


if __name__ == "__main__":
    main()
