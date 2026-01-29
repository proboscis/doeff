"""
Basic CESK Interpreter Usage
============================

This example demonstrates the fundamental usage of the CESK interpreter
with the @do decorator for defining effectful programs.

The CESK machine (Control, Environment, Store, Kontinuation) provides:
- Clean semantics based on abstract machine model
- Explicit control flow via continuation frames
- Effect handling with proper scoping
"""

from doeff import do
from doeff.cesk import run_sync
from doeff.effects import Pure, ask, get, listen, local, put, tell

# =============================================================================
# Example 1: Simple @do function returning a value
# =============================================================================


@do
def hello():
    """A simple program that returns a greeting."""
    return "Hello, CESK!"


def example_simple():
    """Run a simple program and get the result."""
    result = run_sync(hello())

    print("=== Example 1: Simple Program ===")
    print(f"Result: {result.value}")
    print(f"Is success: {result.is_ok}")
    print()


# =============================================================================
# Example 2: Composing @do functions
# =============================================================================


@do
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@do
def multiply(a: int, b: int):
    """Multiply two numbers."""
    return a * b


@do
def calculate():
    """Compose multiple @do functions."""
    sum_result = yield add(3, 4)  # 7
    product = yield multiply(sum_result, 2)  # 14
    return product


def example_composition():
    """Demonstrate composing multiple @do functions."""
    result = run_sync(calculate())

    print("=== Example 2: Composition ===")
    print(f"3 + 4 = 7, then 7 * 2 = {result.value}")
    print()


# =============================================================================
# Example 3: Using Pure effect for explicit values
# =============================================================================


@do
def with_pure():
    """Use Pure effect to yield explicit values."""
    x = yield Pure(42)
    y = yield Pure(8)
    return x + y


def example_pure():
    """Demonstrate Pure effect usage."""
    result = run_sync(with_pure())

    print("=== Example 3: Pure Effect ===")
    print(f"Pure(42) + Pure(8) = {result.value}")
    print()


# =============================================================================
# Example 4: Reader effect (ask/local)
# =============================================================================


@do
def greet_user():
    """Read a value from the environment."""
    name = yield ask("username")
    return f"Hello, {name}!"


@do
def with_local_override():
    """Override environment locally."""
    original = yield greet_user()

    # local() creates a new scope with modified environment
    @do
    def inner():
        return (yield greet_user())

    modified = yield local({"username": "Guest"}, inner())

    return (original, modified)


def example_reader():
    """Demonstrate ask/local effects."""
    # Provide initial environment
    result = run_sync(greet_user(), env={"username": "Alice"})
    print("=== Example 4: Reader Effect ===")
    print(f"Greeting: {result.value}")

    result2 = run_sync(with_local_override(), env={"username": "Bob"})
    print(f"Original: {result2.value[0]}")
    print(f"With local override: {result2.value[1]}")
    print()


# =============================================================================
# Example 5: State effect (get/put)
# =============================================================================


@do
def counter():
    """Use state to maintain a counter."""
    count = yield get("counter")
    yield put("counter", count + 1)
    return count


@do
def count_three_times():
    """Call counter three times."""
    first = yield counter()
    second = yield counter()
    third = yield counter()
    return (first, second, third)


def example_state():
    """Demonstrate get/put state effects."""
    from doeff.cesk import Store

    # Initialize store with counter = 0
    initial_store = Store({"counter": 0})
    result = run_sync(count_three_times(), store=initial_store)

    print("=== Example 5: State Effect ===")
    print(f"Counter values: {result.value}")
    print()


# =============================================================================
# Example 6: Writer effect (tell/listen)
# =============================================================================


@do
def log_steps():
    """Log messages during computation."""
    yield tell("Starting computation...")
    x = yield Pure(10)
    yield tell(f"Got value: {x}")
    y = yield Pure(20)
    yield tell(f"Got another value: {y}")
    yield tell("Done!")
    return x + y


@do
def capture_logs():
    """Capture logs from a sub-computation."""
    result = yield listen(log_steps())
    value, logs = result.value, result.log
    return (value, logs)


def example_writer():
    """Demonstrate tell/listen effects."""
    result = run_sync(capture_logs())
    value, logs = result.value

    print("=== Example 6: Writer Effect ===")
    print(f"Result: {value}")
    print("Captured logs:")
    for log in logs:
        print(f"  - {log}")
    print()


# =============================================================================
# Main
# =============================================================================


def main():
    """Run all examples."""
    example_simple()
    example_composition()
    example_pure()
    example_reader()
    example_state()
    example_writer()

    print("All examples completed successfully!")


if __name__ == "__main__":
    main()
