"""Test the @do decorator with comprehensive monad support."""

import asyncio
import inspect
from collections.abc import Generator
from typing import Any

import pytest

from doeff import (
    Effect,
    ExecutionContext,
    Gather,
    Program,
    ProgramInterpreter,
    Safe,
    annotate,
    ask,
    await_,
    do,
    get,
    listen,
    modify,
    put,
    step,
    tell,
)


# Example 1: Simple program with @do
@do
def simple_program(n: int) -> Generator[Effect, Any, int]:
    """Simple program using @do decorator."""
    config = yield ask("multiplier")
    result = n * config
    yield step(result, meta={"input": n})
    return result


# Example 2: Complex program with all monad types
@do
def complex_program(name: str) -> Generator[Effect, Any, dict]:
    """Complex program using all monad types."""
    # Reader
    yield ask("config")

    # State
    yield put("name", name)
    yield put("counter", 0)

    # Writer
    yield tell(f"Starting program for {name}")

    # IO - print effect removed, using tell instead
    yield tell(f"Processing {name}...")

    # Future
    data = yield await_(fetch_user_data(name))

    # Graph
    yield step(data, meta={"user": name})

    # Error handling using Safe
    safe_result = yield Safe(risky_operation())
    safe_result = safe_result.value if safe_result.is_ok() else (yield safe_recovery(safe_result.error))

    # Parallel async using Gather with Programs
    @do
    def item_worker(item: int) -> Generator[Effect, Any, int]:
        result = yield await_(process_item(item))
        return result

    results = yield Gather(item_worker(1), item_worker(2), item_worker(3))

    # State modification
    yield modify("counter", lambda x: x + sum(results))

    # Listen to sub-computation
    value, log = yield listen(sub_computation())

    # Final state
    final_counter = yield get("counter")

    # Annotate graph
    yield annotate({"completed": True, "final_count": final_counter})

    return {
        "name": name,
        "data": data,
        "safe_result": safe_result,
        "parallel_results": results,
        "sub_value": value,
        "sub_log_size": len(log),
        "final_counter": final_counter,
    }


# Helper functions (also using @do)
@do
def risky_operation() -> Generator[Effect, Any, str]:
    """Operation that might fail."""
    risk = yield ask("risk_level")
    if risk > 0.5:
        raise ValueError(f"Risk too high: {risk}")
    return "success"


@do
def safe_recovery(error: Exception) -> Generator[Effect, Any, str]:
    """Recovery from error."""
    yield tell(f"Recovered from: {error}")
    return "recovered"


@do
def sub_computation() -> Generator[Effect, Any, int]:
    """Sub-computation with logging."""
    yield tell("Sub computation started")
    for i in range(3):
        yield tell(f"Step {i}")
        yield put(f"sub_{i}", i * 10)
    yield tell("Sub computation completed")
    return 42


# Async helper functions
async def fetch_user_data(name: str) -> dict:
    """Simulate fetching user data."""
    await asyncio.sleep(0.01)
    return {"name": name, "id": 123}


async def process_item(item: int) -> int:
    """Process an item asynchronously."""
    await asyncio.sleep(0.005)
    return item * 2


# Test deep chains with @do
@do
def deep_chain_program(depth: int) -> Generator[Effect, Any, int]:
    """Test deep chains without stack overflow."""
    yield put("total", 0)
    yield tell(f"Starting deep chain with depth {depth}")

    for i in range(depth):
        # Multiple effects per iteration
        multiplier = yield ask("multiplier")
        current = yield get("total")
        yield put("total", current + multiplier)

        if i % 1000 == 0:
            yield tell(f"Milestone: {i}")
            yield step(current, meta={"milestone": i})

        if i % 2000 == 0:
            yield annotate({"progress": i / depth})

    final = yield get("total")
    yield tell(f"Completed {depth} iterations with total: {final}")
    return final


@pytest.mark.asyncio
async def test_simple():
    """Test simple program with @do."""
    engine = ProgramInterpreter()
    context = ExecutionContext(env={"multiplier": 3})

    # Note: simple_program(5) returns a Program, not a generator!
    program = simple_program(5)
    result = await engine.run_async(program, context)

    assert result.is_ok
    assert result.value == 15
    print(f"✅ Simple program: {result.value}")


@pytest.mark.asyncio
async def test_complex():
    """Test complex program with all monad types."""
    engine = ProgramInterpreter()
    context = ExecutionContext(
        env={"config": {"version": "1.0"}, "risk_level": 0.3, "multiplier": 2}
    )

    program = complex_program("Alice")
    result = await engine.run_async(program, context)

    print(f"   Result type: {type(result.result)}")
    print(f"   Result: {result.result}")

    assert result.is_ok(), f"Expected Ok, got {result.result}"
    assert result.value["name"] == "Alice"
    assert result.value["final_counter"] == 12  # (1+2+3)*2
    print(f"✅ Complex program completed for {result.value['name']}")
    print(f"   Final counter: {result.value['final_counter']}")
    print(f"   Sub-computation value: {result.value['sub_value']}")


@pytest.mark.asyncio
async def test_deep_chain():
    """Test deep chains with @do decorator."""
    engine = ProgramInterpreter()
    context = ExecutionContext(env={"multiplier": 1})

    # Test with 10,000 iterations
    program = deep_chain_program(10000)
    result = await engine.run_async(program, context)

    assert result.is_ok
    assert result.value == 10000
    print(f"✅ Deep chain completed: {result.value} (no stack overflow!)")


@pytest.mark.asyncio
async def test_composition():
    """Test composing programs created with @do."""

    @do
    def program_a(x: int) -> Generator[Effect, Any, int]:
        yield tell(f"Program A: {x}")
        return x * 2

    @do
    def program_b(y: int) -> Generator[Effect, Any, int]:
        yield tell(f"Program B: {y}")
        return y + 10

    @do
    def composed_program(n: int) -> Generator[Effect, Any, int]:
        a_safe = yield Safe(program_a(n))
        if a_safe.is_err():
            yield tell(f"A failed: {a_safe.error}")
            a_result = 0
        else:
            a_result = a_safe.value

        b_safe = yield Safe(program_b(a_result))
        if b_safe.is_err():
            yield tell(f"B failed: {b_safe.error}")
            b_result = 0
        else:
            b_result = b_safe.value

        return b_result

    engine = ProgramInterpreter()
    context = ExecutionContext()

    program = composed_program(5)
    result = await engine.run_async(program, context)

    assert result.is_ok
    assert result.value == 20  # (5 * 2) + 10
    assert len(result.log) == 2
    print(f"✅ Composed programs: {result.value}")
    print(f"   Log: {result.log}")


def test_do_preserves_metadata_and_program_repr():
    """Ensure @do preserves metadata and Program repr shows original function name."""

    def metadata_program(x: int, y: int = 1) -> Generator[Effect | Program, Any, int]:
        """Original generator for metadata preservation tests."""

        value = yield Program.pure(x + y)
        return value

    decorated = do(metadata_program)

    assert decorated.__name__ == metadata_program.__name__
    assert decorated.__qualname__ == metadata_program.__qualname__
    assert decorated.__doc__ == metadata_program.__doc__
    assert decorated.__module__ == metadata_program.__module__
    assert decorated.__annotations__ == metadata_program.__annotations__
    assert getattr(decorated, "_metadata_source") is metadata_program
    assert inspect.signature(decorated) == inspect.signature(metadata_program)

    program_instance = decorated(3)
    assert "metadata_program" in repr(program_instance)


async def main():
    """Run all tests."""
    print("Testing @do decorator with comprehensive monad support")
    print("=" * 60)

    await test_simple()
    await test_complex()
    await test_deep_chain()
    await test_composition()

    print("\n🎉 All @do decorator tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
