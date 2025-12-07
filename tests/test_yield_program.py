"""Test that the pragmatic engine can handle yielded Programs."""

import asyncio
from collections.abc import Generator
from typing import Any

import pytest

from doeff import (
    Annotate,
    Effect,
    ExecutionContext,
    Get,
    Log,
    Modify,
    ProgramInterpreter,
    Put,
    Step,
    do,
)


@do
def sub_program(x: int) -> Generator[Effect, Any, int]:
    """A sub-program that can be yielded."""
    yield Log(f"sub_program called with x={x}")
    yield Step(x * 2, {"op": "multiply", "input": x, "factor": 2})
    result = x * 2
    yield Annotate({"sub_result": result})
    return result


@do
def another_sub(y: int) -> Generator[Effect, Any, str]:
    """Another sub-program."""
    yield Log(f"another_sub called with y={y}")
    yield Put("last_value", y)
    return f"Result is {y}"


@do
def main_program(n: int) -> Generator[Effect, Any, dict]:
    """Main program that yields other Programs."""
    yield Log("Starting main program")
    yield Put("initial", n)

    # Yield a Program directly (should work now!)
    doubled = yield sub_program(n)
    yield Log(f"sub_program returned: {doubled}")

    # Yield another Program
    message = yield another_sub(doubled)
    yield Log(f"another_sub returned: {message}")

    # Use state operations
    yield Modify("counter", lambda x: (x or 0) + 1)
    counter = yield Get("counter")

    # Yield nested Programs
    tripled = yield sub_program(doubled)

    return {
        "input": n,
        "doubled": doubled,
        "tripled": tripled,
        "message": message,
        "counter": counter,
    }


@do
def deeply_nested(depth: int, value: int) -> Generator[Effect, Any, int]:
    """Test deeply nested Program yields."""
    if depth <= 0:
        yield Log(f"Base case reached with value={value}")
        return value

    yield Log(f"Depth {depth}, value={value}")
    # Yield another Program recursively
    result = yield deeply_nested(depth - 1, value + 1)
    return result + depth


@do
def program_with_mixed_yields() -> Generator[Effect, Any, list]:
    """Test mixing Effect and Program yields."""
    results = []

    # Yield an Effect
    yield Step("step1", {"index": 0})
    results.append("step1")

    # Yield a Program
    value = yield sub_program(5)
    results.append(value)

    # Yield another Effect
    yield Annotate({"progress": "halfway"})
    results.append("annotated")

    # Yield another Program
    msg = yield another_sub(value)
    results.append(msg)

    # Yield an Effect
    yield Log("Completed mixed yields")
    results.append("logged")

    return results


@pytest.mark.asyncio
async def test_basic_program_yield():
    """Test basic yielding of Programs."""
    engine = ProgramInterpreter()
    context = ExecutionContext()

    result = await engine.run_async(main_program(5), context)
    assert result.is_ok
    assert result.value["input"] == 5
    assert result.value["doubled"] == 10
    assert result.value["tripled"] == 20
    assert result.value["message"] == "Result is 10"
    assert result.value["counter"] == 1

    # Check state was modified
    assert context.state["initial"] == 5
    assert context.state["last_value"] == 10
    assert context.state["counter"] == 1

    # Check log entries
    assert any("sub_program called with x=5" in str(entry) for entry in context.log)
    assert any("another_sub called with y=10" in str(entry) for entry in context.log)

    print("✅ Basic program yield test passed")


@pytest.mark.asyncio
async def test_deeply_nested_yields():
    """Test deeply nested Program yields."""
    engine = ProgramInterpreter()
    context = ExecutionContext()

    result = await engine.run_async(deeply_nested(5, 0), context)
    assert result.is_ok
    # Result should be: base_value(5) + sum(1..5) = 5 + 15 = 20
    assert result.value == 20

    # Check we have log entries for each depth
    assert len([e for e in context.log if "Depth" in str(e)]) == 5
    assert any("Base case reached with value=5" in str(entry) for entry in context.log)

    print("✅ Deeply nested yields test passed")


@pytest.mark.asyncio
async def test_mixed_yields():
    """Test mixing Effect and Program yields."""
    engine = ProgramInterpreter()
    context = ExecutionContext()

    result = await engine.run_async(program_with_mixed_yields(), context)
    assert result.is_ok
    assert result.value == ["step1", 10, "annotated", "Result is 10", "logged"]

    # Check graph has steps - meta might be a dict or FrozenDict
    has_index_0 = False
    has_multiply = False
    for step in context.graph.steps:
        if step.meta:
            meta_str = str(step.meta)
            # Check for index: 0 which corresponds to our step1
            if "'index': 0" in meta_str or '"index": 0' in meta_str or "'index', 0" in meta_str:
                has_index_0 = True
            if "multiply" in meta_str:
                has_multiply = True
    assert has_index_0, (
        f"Could not find index=0 in graph steps: {[str(s.meta) for s in context.graph.steps]}"
    )
    assert has_multiply, (
        f"Could not find multiply in graph steps: {[str(s.meta) for s in context.graph.steps]}"
    )

    print("✅ Mixed yields test passed")


@pytest.mark.asyncio
async def test_program_yield_with_error():
    """Test error handling when yielding Programs."""

    @do
    def failing_program() -> Generator[Effect, Any, int]:
        yield Log("About to fail")
        raise ValueError("Intentional error")

    @do
    def main_with_error() -> Generator[Effect, Any, str]:
        try:
            # This should propagate the error
            result = yield failing_program()
            return f"Got {result}"
        except ValueError as e:
            yield Log(f"Caught error: {e}")
            return "Handled error"

    engine = ProgramInterpreter()
    context = ExecutionContext()

    # Test that error propagates through yielded Program
    result = await engine.run_async(failing_program(), context)
    assert result.is_err
    # Unwrap EffectFailure if needed
    error = result.result.error
    from doeff.types import EffectFailure
    if isinstance(error, EffectFailure):
        error = error.cause
    assert "Intentional error" in str(error)

    # Test error handling with yielded Program
    context2 = ExecutionContext()
    result2 = await engine.run_async(main_with_error(), context2)

    # With native try-except support, the error should be caught by the
    # try-except block in main_with_error and handled successfully
    assert result2.is_ok
    assert result2.value == "Handled error"
    # The log should contain the error message from the except block
    assert any("Caught error" in str(entry) for entry in result2.log)

    print("✅ Error handling test passed")


@pytest.mark.asyncio
async def test_state_threading_through_programs():
    """Test that state is properly threaded through yielded Programs."""

    @do
    def increment_counter() -> Generator[Effect, Any, int]:
        current = yield Get("counter")
        new_value = (current or 0) + 1
        yield Put("counter", new_value)
        return new_value

    @do
    def main_counter() -> Generator[Effect, Any, list]:
        results = []

        # Initialize counter
        yield Put("counter", 0)

        # Yield the increment program multiple times
        for _ in range(3):
            value = yield increment_counter()
            results.append(value)

        final = yield Get("counter")
        results.append(final)
        return results

    engine = ProgramInterpreter()
    context = ExecutionContext()

    result = await engine.run_async(main_counter(), context)
    assert result.is_ok
    assert result.value == [1, 2, 3, 3]  # Three increments, then final read
    assert context.state["counter"] == 3

    print("✅ State threading test passed")


async def main():
    """Run all tests."""
    print("Testing Program yields in pragmatic free monad")
    print("=" * 60)

    await test_basic_program_yield()
    await test_deeply_nested_yields()
    await test_mixed_yields()
    await test_program_yield_with_error()
    await test_state_threading_through_programs()

    print("\n🎉 All Program yield tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
