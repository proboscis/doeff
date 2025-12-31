"""
Execution Observability
=======================

This example demonstrates how to monitor CESK interpreter execution
using the observability API. You can:

1. Observe each interpreter step via callbacks
2. Inspect the K (continuation) stack
3. See which effect is being processed
4. Track execution progress and status
"""

from doeff import do
from doeff.cesk import run_sync
from doeff.cesk_observability import ExecutionSnapshot, KFrameSnapshot
from doeff.effects import Pure, catch, finally_, get, put
from doeff.storage import InMemoryStorage


# =============================================================================
# Example 1: Basic step callback
# =============================================================================


@do
def simple_computation():
    """A simple computation to observe."""
    a = yield Pure(10)
    b = yield Pure(20)
    c = yield Pure(30)
    return a + b + c


def example_step_callback():
    """Show basic step-by-step observation."""
    print("=== Example 1: Basic Step Callback ===")

    step_count = 0

    def on_step(snapshot: ExecutionSnapshot):
        nonlocal step_count
        step_count += 1
        print(f"  Step {step_count}: status={snapshot.status}, K depth={len(snapshot.k_stack)}")

    result = run_sync(simple_computation(), on_step=on_step)

    print(f"\nTotal steps: {step_count}")
    print(f"Final result: {result.value}")
    print()


# =============================================================================
# Example 2: Observing K stack depth
# =============================================================================


@do
def nested_level_3():
    """Innermost function."""
    return (yield Pure("level 3 result"))


@do
def nested_level_2():
    """Middle function."""
    x = yield Pure("level 2 start")
    result = yield nested_level_3()
    return f"level 2 wrapping: {result}"


@do
def nested_level_1():
    """Outer function."""
    x = yield Pure("level 1 start")
    result = yield nested_level_2()
    return f"level 1 wrapping: {result}"


def example_k_stack_depth():
    """Show K stack growing and shrinking during nested calls."""
    print("=== Example 2: K Stack Depth During Nested Calls ===")

    max_depth = 0
    depth_history = []

    def on_step(snapshot: ExecutionSnapshot):
        nonlocal max_depth
        depth = len(snapshot.k_stack)
        depth_history.append(depth)
        max_depth = max(max_depth, depth)

    result = run_sync(nested_level_1(), on_step=on_step)

    print(f"K stack depth over time: {depth_history[:20]}{'...' if len(depth_history) > 20 else ''}")
    print(f"Maximum K stack depth: {max_depth}")
    print(f"Final result: {result.value}")
    print()


# =============================================================================
# Example 3: Inspecting K frame types
# =============================================================================


@do
def cleanup_action():
    """Cleanup that runs in finally block."""
    yield Pure("cleanup done")
    return None


@do
def workflow_with_frames():
    """A workflow that creates various K frame types."""
    # This creates a FinallyFrame
    @do
    def inner_with_finally():
        x = yield Pure(42)
        return x * 2

    result = yield finally_(inner_with_finally(), cleanup_action())

    # This creates a CatchFrame
    @do
    def might_fail():
        return (yield Pure("success"))

    @do
    def handle_error(e):
        return f"handled: {e}"

    safe_result = yield catch(might_fail(), handle_error)

    return (result, safe_result)


def example_frame_types():
    """Show different K frame types during execution."""
    print("=== Example 3: K Frame Types ===")

    frame_types_seen = set()

    def on_step(snapshot: ExecutionSnapshot):
        for frame in snapshot.k_stack:
            frame_types_seen.add(frame.frame_type)

    result = run_sync(workflow_with_frames(), on_step=on_step)

    print(f"Frame types observed: {sorted(frame_types_seen)}")
    print(f"Final result: {result.value}")
    print()


# =============================================================================
# Example 4: Observing current effect
# =============================================================================


@do
def workflow_with_effects():
    """A workflow using various effects."""
    # Pure effect
    x = yield Pure(100)

    # State effects
    yield put("counter", 0)
    count = yield get("counter")
    yield put("counter", count + 1)

    # More Pure
    y = yield Pure(200)

    return x + y


def example_current_effect():
    """Show which effect is being processed at each step."""
    print("=== Example 4: Current Effect Observation ===")

    effects_seen = []

    def on_step(snapshot: ExecutionSnapshot):
        if snapshot.current_effect is not None:
            effect_type = type(snapshot.current_effect).__name__
            effects_seen.append(effect_type)

    from doeff.cesk import Store

    result = run_sync(
        workflow_with_effects(), store=Store({"counter": 0}), on_step=on_step
    )

    print(f"Effects processed: {effects_seen}")
    print(f"Final result: {result.value}")
    print()


# =============================================================================
# Example 5: Execution status tracking
# =============================================================================


@do
def long_workflow():
    """A workflow with many steps."""
    total = 0
    for i in range(10):
        x = yield Pure(i)
        total += x
    return total


def example_status_tracking():
    """Track execution status through the workflow."""
    print("=== Example 5: Execution Status Tracking ===")

    status_counts = {"pending": 0, "running": 0, "paused": 0, "completed": 0, "failed": 0}

    def on_step(snapshot: ExecutionSnapshot):
        status_counts[snapshot.status] += 1

    result = run_sync(long_workflow(), on_step=on_step)

    print(f"Status counts: {status_counts}")
    print(f"Final result: {result.value}")
    print()


# =============================================================================
# Example 6: Effect call trace observation
# =============================================================================


@do
def outer_function():
    """Outer function in the call chain."""
    x = yield Pure("outer start")
    result = yield middle_function()
    return f"outer got: {result}"


@do
def middle_function():
    """Middle function in the call chain."""
    x = yield Pure("middle start")
    result = yield inner_function()
    return f"middle got: {result}"


@do
def inner_function():
    """Inner function in the call chain."""
    return (yield Pure("inner result"))


def example_effect_trace():
    """Observe the effect call trace (chain of @do functions)."""
    print("=== Example 6: Effect Call Trace ===")

    captured_snapshot = None
    max_depth = 0

    def on_step(snapshot: ExecutionSnapshot):
        nonlocal captured_snapshot, max_depth
        if len(snapshot.k_stack) > max_depth:
            max_depth = len(snapshot.k_stack)
            captured_snapshot = snapshot

    result = run_sync(outer_function(), on_step=on_step)

    # Show the effect trace as a call chain
    if captured_snapshot:
        trace = []
        for frame in reversed(captured_snapshot.k_stack):
            if frame.location:
                trace.append(frame.location.function)
        print(f"Effect trace: {' -> '.join(trace)}")
        print()

        # Also show detailed frame info
        print(f"Detailed view ({max_depth} frames):")
        for i, frame in enumerate(captured_snapshot.k_stack):
            if frame.location:
                # Extract just filename without full path
                filename = frame.location.filename.split("/")[-1]
                print(f"  [{i}] {frame.location.function} at {filename}:{frame.location.line}")

    print(f"\nFinal result: {result.value}")
    print()


# =============================================================================
# Example 7: Progress logging with cache
# =============================================================================


@do
def workflow_with_cache():
    """A workflow that uses cache (observable storage access)."""
    from doeff.effects import cacheget, cacheput

    # Check cache
    cached = yield cacheget("result")
    if cached is not None:
        return cached

    # Compute
    x = yield Pure(10)
    y = yield Pure(20)
    result = x + y

    # Cache result
    yield cacheput("result", result)

    return result


def example_cache_with_observability():
    """Observe workflow execution with cache operations."""
    print("=== Example 7: Observability with Cache ===")

    storage = InMemoryStorage()
    steps = []

    def on_step(snapshot: ExecutionSnapshot):
        effect_name = (
            type(snapshot.current_effect).__name__
            if snapshot.current_effect
            else "None"
        )
        steps.append((snapshot.step_count, effect_name, len(snapshot.k_stack)))

    # First run - cache miss
    print("First run (cache miss):")
    result1 = run_sync(workflow_with_cache(), storage=storage, on_step=on_step)
    print(f"  Result: {result1.value}")
    print(f"  Steps: {len(steps)}")

    steps.clear()

    # Second run - cache hit
    print("\nSecond run (cache hit):")
    result2 = run_sync(workflow_with_cache(), storage=storage, on_step=on_step)
    print(f"  Result: {result2.value}")
    print(f"  Steps: {len(steps)} (fewer steps due to cache hit)")
    print()


# =============================================================================
# Main
# =============================================================================


def main():
    """Run all examples."""
    example_step_callback()
    example_k_stack_depth()
    example_frame_types()
    example_current_effect()
    example_status_tracking()
    example_effect_trace()
    example_cache_with_observability()

    print("All observability examples completed!")


if __name__ == "__main__":
    main()
