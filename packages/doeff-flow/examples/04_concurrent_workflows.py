"""
Concurrent Workflows with Live Observability
=============================================

This example demonstrates running multiple workflows concurrently,
each with its own trace file that can be watched independently.

Run this example:
    cd packages/doeff-flow
    uv run python examples/04_concurrent_workflows.py

Watch all workflows:
    doeff-flow ps

Watch a specific workflow:
    doeff-flow watch worker-001 --exit-on-complete
"""

import random
import threading
import time
from datetime import datetime
from pathlib import Path

from doeff import do
from doeff.effects import Pure

from doeff_flow import run_workflow


# =============================================================================
# Worker Workflows
# =============================================================================


@do
def process_task(task_id: str, complexity: int):
    """Process a single task with variable complexity."""
    steps = complexity
    results = []

    for step in range(steps):
        # Simulate work
        time.sleep(0.1)
        result = yield Pure(f"task-{task_id}-step-{step}")
        results.append(result)

    final = yield Pure({
        "task_id": task_id,
        "steps_completed": len(results),
        "results": results,
    })
    return final


@do
def worker_workflow(worker_id: str, num_tasks: int):
    """
    Worker that processes multiple tasks.
    Each worker runs independently and has its own trace.
    """
    print(f"[Worker {worker_id}] Starting with {num_tasks} tasks")
    start_time = datetime.now()

    completed_tasks = []

    for i in range(num_tasks):
        task_id = f"{worker_id}-task-{i:02d}"
        complexity = random.randint(2, 5)

        print(f"[Worker {worker_id}] Processing {task_id} (complexity: {complexity})")
        result = yield process_task(task_id, complexity)
        completed_tasks.append(result)
        print(f"[Worker {worker_id}] Completed {task_id}")

    elapsed = (datetime.now() - start_time).total_seconds()

    summary = yield Pure({
        "worker_id": worker_id,
        "tasks_completed": len(completed_tasks),
        "elapsed_seconds": round(elapsed, 2),
        "tasks": completed_tasks,
    })

    print(f"[Worker {worker_id}] Finished in {elapsed:.2f}s")
    return summary


# =============================================================================
# Orchestrator
# =============================================================================


def run_worker(worker_id: str, num_tasks: int, trace_dir: Path, results: dict):
    """Run a single worker in a thread."""
    result = run_workflow(
        worker_workflow(worker_id, num_tasks),
        workflow_id=f"worker-{worker_id}",
        trace_dir=trace_dir,
    )
    results[worker_id] = result


def run_concurrent_workers(num_workers: int, tasks_per_worker: int):
    """Run multiple workers concurrently."""
    trace_dir = Path(".doeff-flow")
    results = {}
    threads = []

    print("\n" + "=" * 60)
    print(f"Starting {num_workers} concurrent workers")
    print(f"Each worker will process {tasks_per_worker} tasks")
    print("=" * 60 + "\n")

    # Start all workers
    start_time = datetime.now()

    for i in range(num_workers):
        worker_id = f"{i + 1:03d}"
        t = threading.Thread(
            target=run_worker,
            args=(worker_id, tasks_per_worker, trace_dir, results),
        )
        threads.append(t)
        t.start()

    # Wait for all workers to complete
    for t in threads:
        t.join()

    elapsed = (datetime.now() - start_time).total_seconds()

    # Summarize results
    print("\n" + "=" * 60)
    print("All Workers Complete")
    print("=" * 60)

    total_tasks = 0
    for worker_id, result in sorted(results.items()):
        if result.is_ok:
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


def main():
    print("=" * 60)
    print("Concurrent Workflows Example")
    print("=" * 60)
    print()
    print("This example runs multiple workers concurrently.")
    print("Each worker has its own trace file.")
    print()
    print("Watch commands:")
    print("  doeff-flow ps                          # List all workflows")
    print("  doeff-flow watch worker-001            # Watch specific worker")
    print()
    input("Press Enter to start workers...")

    # Run 3 workers, each processing 4 tasks
    results = run_concurrent_workers(
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
