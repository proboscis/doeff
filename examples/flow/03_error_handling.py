"""
Error Handling with Live Observability
======================================

This example demonstrates how workflow failures are captured in traces,
allowing you to see exactly where and why a workflow failed.

Run this example:
    python examples/flow/03_error_handling.py

Watch the execution:
    doeff-flow watch error-demo --exit-on-complete

After completion, inspect the trace:
    doeff-flow history error-demo
"""

import random
import time
from pathlib import Path

from doeff import do
from doeff.effects import Pure

from doeff_flow import run_workflow


# =============================================================================
# Simulated External Services
# =============================================================================


@do
def call_external_api(endpoint: str, attempt: int = 1):
    """Simulate calling an external API that might fail."""
    print(f"  [API] Calling {endpoint} (attempt {attempt})...")
    time.sleep(0.1)

    # Simulate random failures
    if random.random() < 0.3:  # 30% failure rate
        raise ConnectionError(f"Failed to connect to {endpoint}")

    response = yield Pure({"status": "ok", "endpoint": endpoint})
    print(f"  [API] Success: {endpoint}")
    return response


@do
def fetch_user(user_id: int):
    """Fetch user data from API."""
    endpoint = f"/users/{user_id}"
    response = yield call_external_api(endpoint)
    user = yield Pure({"id": user_id, "name": f"User{user_id}", **response})
    return user


@do
def fetch_orders(user_id: int):
    """Fetch orders for a user."""
    endpoint = f"/users/{user_id}/orders"
    response = yield call_external_api(endpoint)
    orders = yield Pure([
        {"order_id": i, "user_id": user_id, "amount": random.randint(10, 100)}
        for i in range(3)
    ])
    return orders


@do
def process_user_data(user_id: int):
    """Process complete user data - may fail at any step."""
    print(f"\n[Processing] User {user_id}")

    # Step 1: Fetch user
    user = yield fetch_user(user_id)
    print(f"  [Data] Got user: {user['name']}")

    # Step 2: Fetch orders
    orders = yield fetch_orders(user_id)
    print(f"  [Data] Got {len(orders)} orders")

    # Step 3: Calculate total
    total = sum(o["amount"] for o in orders)
    result = yield Pure({
        "user": user,
        "orders": orders,
        "total_amount": total,
    })

    print(f"  [Data] Total amount: ${total}")
    return result


# =============================================================================
# Workflow with Error Handling
# =============================================================================


@do
def workflow_with_errors():
    """
    Workflow that processes multiple users.
    Some may fail, demonstrating error capture in traces.
    """
    print("\n" + "=" * 60)
    print("Starting Workflow (with potential failures)")
    print("=" * 60)

    results = []
    failed = []

    for user_id in [1, 2, 3, 4, 5]:
        try:
            result = yield process_user_data(user_id)
            results.append(result)
        except ConnectionError as e:
            print(f"  [ERROR] User {user_id}: {e}")
            failed.append({"user_id": user_id, "error": str(e)})
            # Continue processing other users

    summary = yield Pure({
        "successful": len(results),
        "failed": len(failed),
        "total_processed": len(results) + len(failed),
        "results": results,
        "failures": failed,
    })

    print("\n" + "=" * 60)
    print(f"Workflow Complete: {len(results)} success, {len(failed)} failed")
    print("=" * 60)

    return summary


@do
def workflow_that_fails():
    """
    Workflow that intentionally fails to demonstrate error tracing.
    """
    print("\n" + "=" * 60)
    print("Starting Workflow (will fail)")
    print("=" * 60)

    # Some successful steps first
    step1 = yield Pure("Step 1 complete")
    print(f"  {step1}")

    step2 = yield Pure("Step 2 complete")
    print(f"  {step2}")

    # This step will fail
    print("  Step 3: About to fail...")
    raise ValueError("Intentional failure for demonstration")


# =============================================================================
# Main
# =============================================================================


def main():
    print("=" * 60)
    print("Error Handling Example")
    print("=" * 60)
    print()
    print("This example demonstrates error capture in workflow traces.")
    print()
    print("Watch commands:")
    print("  doeff-flow watch error-demo --exit-on-complete")
    print("  doeff-flow watch failing-demo --exit-on-complete")
    print()

    # Example 1: Workflow with recoverable errors
    print("\n--- Example 1: Workflow with recoverable errors ---")
    result1 = run_workflow(
        workflow_with_errors(),
        workflow_id="error-demo",
        trace_dir=Path(".doeff-flow"),
    )

    if result1.is_ok:
        print(f"\nResult: {result1.value['successful']} successful, "
              f"{result1.value['failed']} failed")
    else:
        print(f"\nWorkflow failed: {result1.error}")

    # Example 2: Workflow that fails completely
    print("\n--- Example 2: Workflow that fails ---")
    result2 = run_workflow(
        workflow_that_fails(),
        workflow_id="failing-demo",
        trace_dir=Path(".doeff-flow"),
    )

    if result2.is_ok:
        print(f"\nResult: {result2.value}")
    else:
        print(f"\nWorkflow failed (expected): {type(result2.error).__name__}")

    # Show how to inspect traces
    print("\n" + "=" * 60)
    print("Inspect the traces with:")
    print("  doeff-flow history error-demo")
    print("  doeff-flow history failing-demo")
    print("=" * 60)


if __name__ == "__main__":
    main()
