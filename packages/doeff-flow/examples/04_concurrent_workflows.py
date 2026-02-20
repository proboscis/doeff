"""
Concurrent Workflows with Live Observability
=============================================

This example demonstrates running multiple workflows concurrently,
each with its own trace file that can be watched independently.

Run this example:
    cd packages/doeff-flow
    uv run python examples/04_concurrent_workflows.py

Watch all workflows (dashboard view):
    doeff-flow watch

Watch a specific workflow:
    doeff-flow watch worker-001 --exit-on-complete

Note: By default, traces are written to ~/.local/state/doeff-flow/ (XDG spec).
"""

import argparse
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from doeff_flow import run_workflow

from doeff import do, slog

# =============================================================================
# Worker Workflows
# =============================================================================


@do
def process_step(task_id: str, step: int):
    """Process a single step of a task."""
    time.sleep(0.1)
    return f"task-{task_id}-step-{step}"


@do
def process_task(task_id: str, complexity: int):
    """Process a single task with variable complexity."""
    results = []

    for step in range(complexity):
        # Simulate work via sub-workflow
        result = yield process_step(task_id, step)
        results.append(result)

    return {
        "task_id": task_id,
        "steps_completed": len(results),
        "results": results,
    }


@do
def worker_workflow(worker_id: str, num_tasks: int):
    """
    Worker that processes multiple tasks.
    Each worker runs independently and has its own trace.
    """
    yield slog(step="worker", worker_id=worker_id, status="starting", num_tasks=num_tasks)
    start_time = datetime.now()  # nosemgrep: doeff-no-datetime-now-in-do

    completed_tasks = []

    for i in range(num_tasks):
        task_id = f"{worker_id}-task-{i:02d}"
        complexity = random.randint(2, 5)  # nosemgrep: doeff-no-random-in-do

        yield slog(
            step="worker",
            worker_id=worker_id,
            status="processing",
            task_id=task_id,
            complexity=complexity,
        )
        result = yield process_task(task_id, complexity)
        completed_tasks.append(result)
        yield slog(step="worker", worker_id=worker_id, status="completed", task_id=task_id)

    elapsed = (
        datetime.now()  # nosemgrep: doeff-no-datetime-now-in-do
        - start_time
    ).total_seconds()

    summary = {
        "worker_id": worker_id,
        "tasks_completed": len(completed_tasks),
        "elapsed_seconds": round(elapsed, 2),
        "tasks": completed_tasks,
    }

    yield slog(step="worker", worker_id=worker_id, status="finished", elapsed=f"{elapsed:.2f}s")
    return summary


# =============================================================================
# Orchestrator
# =============================================================================


def run_worker(worker_id: str, num_tasks: int, results: dict):
    """Run a single worker in a thread."""
    result = run_workflow(
        worker_workflow(worker_id, num_tasks),
        workflow_id=f"worker-{worker_id}",
    )
    results[worker_id] = result


def run_concurrent_workers(num_workers: int, tasks_per_worker: int):
    """Run multiple workers concurrently."""
    results = {}

    print("\n" + "=" * 60)
    print(f"Starting {num_workers} concurrent workers")
    print(f"Each worker will process {tasks_per_worker} tasks")
    print("=" * 60 + "\n")

    # Start all workers
    start_time = datetime.now()  # nosemgrep: doeff-no-datetime-now-in-do

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = []
        for i in range(num_workers):
            worker_id = f"{i + 1:03d}"
            futures.append(executor.submit(run_worker, worker_id, tasks_per_worker, results))

        for future in futures:
            future.result()

    elapsed = (
        datetime.now()  # nosemgrep: doeff-no-datetime-now-in-do
        - start_time
    ).total_seconds()

    # Summarize results
    print("\n" + "=" * 60)
    print("All Workers Complete")
    print("=" * 60)

    total_tasks = 0
    for worker_id, result in sorted(results.items()):
        if result.is_ok():
            tasks = result.value["tasks_completed"]
            worker_elapsed = result.value["elapsed_seconds"]
            total_tasks += tasks
            print(f"  Worker {worker_id}: {tasks} tasks in {worker_elapsed}s")
        else:
            print(f"  Worker {worker_id}: FAILED - {result.error}")

    print(f"\nTotal: {total_tasks} tasks completed in {elapsed:.2f}s")
    print(f"Throughput: {total_tasks / elapsed:.1f} tasks/second")

    return results


# =============================================================================
# Main
# =============================================================================


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Run concurrent workflow workers.")
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Start immediately without waiting for interactive confirmation.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("Concurrent Workflows Example")
    print("=" * 60)
    print()
    print("This example runs multiple workers concurrently.")
    print("Each worker has its own trace file.")
    print()
    print("Watch commands:")
    print("  doeff-flow watch                       # Dashboard view of all")
    print("  doeff-flow ps                          # List all workflows")
    print("  doeff-flow watch worker-001            # Watch specific worker")
    print()
    if not args.no_wait and sys.stdin.isatty():
        input("Press Enter to start workers...")
    else:
        print("Skipping wait prompt (--no-wait or non-interactive stdin detected).")

    # Run 3 workers, each processing 4 tasks
    run_concurrent_workers(
        num_workers=3,
        tasks_per_worker=4,
    )

    print("\n" + "=" * 60)
    print("Trace files created:")
    print("=" * 60)
    print()
    print("Inspect traces with:")
    print("  doeff-flow ps")
    print("  doeff-flow history worker-001")
    print("  doeff-flow history worker-002")
    print("  doeff-flow history worker-003")


if __name__ == "__main__":
    main()
