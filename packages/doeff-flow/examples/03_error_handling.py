"""
Error Handling with Live Observability
======================================

This example demonstrates how workflow failures are captured in traces,
allowing you to see exactly where and why a workflow failed.

Run this example:
    cd packages/doeff-flow
    uv run python examples/03_error_handling.py

Watch the execution:
    doeff-flow watch error-demo --exit-on-complete

After completion, inspect the trace:
    doeff-flow history error-demo

Note: By default, traces are written to ~/.local/state/doeff-flow/ (XDG spec).
"""

import random
import time

from doeff import do

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

    print(f"  [API] Success: {endpoint}")
    return {"status": "ok", "endpoint": endpoint}


@do
def fetch_user(user_id: int):
    """Fetch user data from API."""
    endpoint = f"/users/{user_id}"
    response = yield call_external_api(endpoint)
    return {"id": user_id, "name": f"User{user_id}", **response}


@do
def fetch_orders(user_id: int):
    """Fetch orders for a user."""
    endpoint = f"/users/{user_id}/orders"
    yield call_external_api(endpoint)
    return [
        {"order_id": i, "user_id": user_id, "amount": random.randint(10, 100)}
        for i in range(3)
    ]


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
    result = {
        "user": user,
        "orders": orders,
        "total_amount": total,
    }

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

    summary = {
        "successful": len(results),
        "failed": len(failed),
        "total_processed": len(results) + len(failed),
        "results": results,
        "failures": failed,
    }

    print("\n" + "=" * 60)
    print(f"Workflow Complete: {len(results)} success, {len(failed)} failed")
    print("=" * 60)

    return summary


@do
def do_step(name: str):
    """Simple step that prints completion."""
    print(f"  {name} complete")
    return name


@do
def workflow_that_fails():
    """
    Workflow that intentionally fails to demonstrate error tracing.
    """
    print("\n" + "=" * 60)
    print("Starting Workflow (will fail)")
    print("=" * 60)

    # Some successful steps first
    yield do_step("Step 1")
    yield do_step("Step 2")

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
