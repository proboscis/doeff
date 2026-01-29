"""
Error Traceback Capture
=======================

This example demonstrates how the CESK interpreter captures rich traceback
information when exceptions occur. You get both:

1. Effect/Kleisli call chain: Which @do functions were called
2. Python call chain: The pure function calls leading to the raise

This provides much better debugging experience than standard Python tracebacks
for effectful code.
"""

from doeff import do
from doeff.cesk import run_sync
from doeff.cesk_traceback import format_traceback, format_traceback_short, to_dict
from doeff.effects import Pure, catch

# =============================================================================
# Example 1: Simple exception with traceback
# =============================================================================


@do
def failing_function():
    """A function that raises an exception."""
    x = yield Pure(42)
    raise ValueError(f"Something went wrong with value: {x}")


def example_simple_error():
    """Show basic error traceback capture."""
    result = run_sync(failing_function())

    print("=== Example 1: Simple Error Traceback ===")
    print(f"Is error: {result.is_err}")
    print(f"Exception type: {type(result.error).__name__}")
    print(f"Exception message: {result.error}")
    print()

    if result.captured_traceback:
        print("Captured Traceback:")
        print(format_traceback(result.captured_traceback))
    print()


# =============================================================================
# Example 2: Nested @do functions (effect chain)
# =============================================================================


@do
def level_3():
    """Innermost function that raises."""
    raise RuntimeError("Error in level 3!")


@do
def level_2():
    """Middle function."""
    yield Pure("Processing in level 2...")
    result = yield level_3()
    return result


@do
def level_1():
    """Outer function."""
    yield Pure("Starting in level 1...")
    result = yield level_2()
    return result


def example_nested_chain():
    """Show effect chain in traceback."""
    result = run_sync(level_1())

    print("=== Example 2: Nested Effect Chain ===")
    print(f"Exception: {result.error}")
    print()

    if result.captured_traceback:
        print("Full traceback:")
        print(format_traceback(result.captured_traceback))

        print("Short format (one-liner):")
        print(format_traceback_short(result.captured_traceback))
    print()


# =============================================================================
# Example 3: Mixed @do and pure Python functions
# =============================================================================


def validate_input(value: int) -> None:
    """Pure Python validation function."""
    if value < 0:
        raise ValueError(f"Value must be non-negative, got: {value}")


def process_data(value: int) -> int:
    """Pure Python processing that calls validation."""
    validate_input(value)
    return value * 2


@do
def workflow_with_pure_functions():
    """@do function that calls pure Python functions."""
    x = yield Pure(-5)  # Negative value will cause error
    result = process_data(x)  # This will raise
    return result


def example_mixed_chain():
    """Show both effect frames and Python frames."""
    result = run_sync(workflow_with_pure_functions())

    print("=== Example 3: Mixed Effect and Python Frames ===")
    print(f"Exception: {result.error}")
    print()

    if result.captured_traceback:
        tb = result.captured_traceback

        print(f"Effect frames ({len(tb.effect_frames)}):")
        for i, frame in enumerate(tb.effect_frames):
            print(f"  [{i}] {frame.location.function} at {frame.location.filename}:{frame.location.lineno}")

        print(f"\nPython frames ({len(tb.python_frames)}):")
        for i, frame in enumerate(tb.python_frames):
            print(f"  [{i}] {frame.location.function} at {frame.location.filename}:{frame.location.lineno}")

        print("\nFull traceback:")
        print(format_traceback(tb))
    print()


# =============================================================================
# Example 4: Using catch to handle errors
# =============================================================================


@do
def risky_operation():
    """An operation that might fail."""
    yield Pure("Starting risky operation...")
    raise OSError("Network connection failed!")


@do
def handle_error(error: BaseException):
    """Error handler that provides a fallback."""
    yield Pure(f"Handling error: {error}")
    return "fallback_value"


@do
def safe_workflow():
    """Use catch to handle errors gracefully."""
    result = yield catch(risky_operation(), handle_error)
    return f"Got result: {result}"


def example_catch_handler():
    """Show error handling with catch."""
    result = run_sync(safe_workflow())

    print("=== Example 4: Error Handling with Catch ===")
    print(f"Is success: {result.is_ok}")
    print(f"Result: {result.value}")
    print()


# =============================================================================
# Example 5: Serializing traceback to dict (for logging)
# =============================================================================


@do
def api_call():
    """Simulate an API call that fails."""
    yield Pure("Calling external API...")
    raise ConnectionError("API timeout after 30s")


def example_serialization():
    """Show traceback serialization for logging/transport."""
    result = run_sync(api_call())

    print("=== Example 5: Traceback Serialization ===")

    if result.captured_traceback:
        data = to_dict(result.captured_traceback)

        print("Serialized traceback (JSON-compatible dict):")
        print(f"  version: {data['version']}")
        print(f"  exception.type: {data['exception']['type']}")
        print(f"  exception.message: {data['exception']['message']}")
        print(f"  effect_frames count: {len(data['effect_frames'])}")
        print(f"  python_frames count: {len(data['python_frames'])}")

        # This can be sent to logging systems, error trackers, etc.
        import json

        json_str = json.dumps(data, indent=2)
        print("\nJSON output (first 500 chars):")
        print(json_str[:500] + "..." if len(json_str) > 500 else json_str)
    print()


# =============================================================================
# Example 6: Accessing traceback details programmatically
# =============================================================================


@do
def compute_with_context():
    """A computation that fails with useful context."""
    user_id = yield Pure(12345)
    order_id = yield Pure("ORD-2024-001")
    # Simulate failure during processing
    raise ValueError(f"Order {order_id} not found for user {user_id}")


def example_programmatic_access():
    """Access traceback details for custom error handling."""
    result = run_sync(compute_with_context())

    print("=== Example 6: Programmatic Traceback Access ===")

    if result.captured_traceback:
        tb = result.captured_traceback

        print(f"Exception type: {tb.exception_type}")
        print(f"Exception message: {tb.exception_message}")
        print(f"Exception args: {tb.exception_args}")
        print(f"Capture timestamp: {tb.capture_timestamp}")

        print("\nEffect call chain:")
        for frame in tb.effect_frames:
            loc = frame.location
            print(f"  -> {loc.function}() at line {loc.lineno}")
            if loc.code:
                print(f"     Code: {loc.code.strip()}")

            if frame.call_site:
                cs = frame.call_site
                print(f"     Called from: {cs.function}() line {cs.lineno}")
    print()


# =============================================================================
# Main
# =============================================================================


def main():
    """Run all examples."""
    example_simple_error()
    example_nested_chain()
    example_mixed_chain()
    example_catch_handler()
    example_serialization()
    example_programmatic_access()

    print("All error traceback examples completed!")


if __name__ == "__main__":
    main()
