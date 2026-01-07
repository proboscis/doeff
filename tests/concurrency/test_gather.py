"""
Interpreter tests for Gather effect.

Tests sequential gathering of Programs, parameterized to run against
both CESK interpreter and ProgramInterpreter.
"""

import asyncio
from typing import TYPE_CHECKING

import pytest

from doeff import Await, Gather, Get, Log, Program, Put, do, EffectGenerator

if TYPE_CHECKING:
    from tests.conftest import Interpreter


@pytest.mark.asyncio
async def test_gather_list_basic(interpreter: "Interpreter") -> None:
    """Test Gather effect with a list of Programs."""
    engine = interpreter

    @do
    def make_value(x: int) -> EffectGenerator[int]:
        yield Log(f"Making value {x}")
        return x * 2

    @do
    def gather_test() -> EffectGenerator[list]:
        programs = [make_value(1), make_value(2), make_value(3)]
        results = yield Gather(*programs)
        yield Log(f"Gathered results: {results}")
        return results

    result = await engine.run_async(gather_test())

    assert result.is_ok
    assert result.value == [2, 4, 6]
    # 3 logs from make_value + 1 from gather_test
    assert len(result.log) == 4
    assert "Making value 1" in result.log[0]
    assert "Making value 2" in result.log[1]
    assert "Making value 3" in result.log[2]
    assert "Gathered results: [2, 4, 6]" in result.log[3]


@pytest.mark.asyncio
async def test_gather_sequential_state_accumulation(interpreter: "Interpreter") -> None:
    """Test Gather runs Programs sequentially with state accumulation.

    Note: CESK runs Gather sequentially (state accumulates),
    Pure runs Gather concurrently (no state accumulation).
    """
    if interpreter.interpreter_type == "pure":
        pytest.skip("ProgramInterpreter runs Gather concurrently, not sequentially")
    engine = interpreter

    @do
    def increment_counter() -> EffectGenerator[int]:
        counter = yield Get("counter")
        counter = (counter or 0) + 1
        yield Put("counter", counter)
        yield Log(f"Counter is now {counter}")
        return counter

    @do
    def gather_state_test() -> EffectGenerator[list]:
        yield Put("counter", 0)
        # Gather runs sequentially - each program sees previous state
        results = yield Gather(
            increment_counter(),
            increment_counter(),
            increment_counter(),
        )
        final_counter = yield Get("counter")
        yield Log(f"Final counter: {final_counter}")
        return results

    result = await engine.run_async(gather_state_test())

    assert result.is_ok
    # Sequential: each sees previous, so [1, 2, 3]
    assert result.value == [1, 2, 3]
    assert result.state["counter"] == 3


@pytest.mark.asyncio
async def test_gather_empty(interpreter: "Interpreter") -> None:
    """Test Gather with empty list returns empty list."""
    engine = interpreter

    @do
    def empty_gather() -> EffectGenerator[list]:
        results = yield Gather()
        return results

    result = await engine.run_async(empty_gather())

    assert result.is_ok
    assert result.value == []


@pytest.mark.asyncio
async def test_gather_with_async(interpreter: "Interpreter") -> None:
    """Test Gather with async operations."""
    engine = interpreter

    async def fetch_data(item_id: int) -> str:
        await asyncio.sleep(0.01)
        return f"Data-{item_id}"

    @do
    def async_prog(item_id: int) -> EffectGenerator[str]:
        data = yield Await(fetch_data(item_id))
        yield Log(f"Fetched: {data}")
        return data

    @do
    def gather_async_test() -> EffectGenerator[list]:
        programs = [async_prog(i) for i in range(3)]
        results = yield Gather(*programs)
        return results

    result = await engine.run_async(gather_async_test())

    assert result.is_ok
    assert result.value == ["Data-0", "Data-1", "Data-2"]
    assert len(result.log) == 3


@pytest.mark.asyncio
async def test_gather_error_propagation(interpreter: "Interpreter") -> None:
    """Test that errors in gathered Programs propagate correctly."""
    engine = interpreter

    @do
    def good_prog(x: int) -> EffectGenerator[int]:
        yield Log(f"Good prog {x}")
        return x

    @do
    def bad_prog() -> EffectGenerator[int]:
        yield Log("About to fail")
        raise ValueError("Intentional error")

    @do
    def gather_with_error() -> EffectGenerator[list]:
        programs = [good_prog(1), bad_prog(), good_prog(2)]
        results = yield Gather(*programs)
        yield Log("This should not be logged")
        return results

    result = await engine.run_async(gather_with_error())

    assert result.is_err
    # Pure wraps errors in EffectFailureError (may be nested), CESK returns original
    error = result.error
    # Unwrap nested EffectFailureError to find the root cause
    while hasattr(error, "cause"):
        error = error.cause
    assert isinstance(error, ValueError)
    assert "Intentional error" in str(error)
    # Verify short-circuiting for CESK (good_prog(2) not run)
    # Pure runs concurrently so behavior differs
    if interpreter.interpreter_type == "cesk":
        assert "Good prog 1" in result.log
        assert "About to fail" in result.log
        assert "Good prog 2" not in result.log
        assert "This should not be logged" not in result.log
        assert result.log.index("Good prog 1") < result.log.index("About to fail")


@pytest.mark.asyncio
async def test_gather_pure_programs(interpreter: "Interpreter") -> None:
    """Test Gather with pure Programs (no effects)."""
    engine = interpreter

    @do
    def gather_pure() -> EffectGenerator[list]:
        programs = [
            Program.pure(10),
            Program.pure(20),
            Program.pure(30),
        ]
        results = yield Gather(*programs)
        return results

    result = await engine.run_async(gather_pure())

    assert result.is_ok
    assert result.value == [10, 20, 30]
    assert len(result.log) == 0


@pytest.mark.asyncio
async def test_gather_nested(interpreter: "Interpreter") -> None:
    """Test nested Gather effects."""
    engine = interpreter

    @do
    def inner_worker(x: int) -> EffectGenerator[int]:
        yield Log(f"Inner {x}")
        return x

    @do
    def outer_worker(start: int) -> EffectGenerator[list[int]]:
        yield Log(f"Outer starting at {start}")
        results = yield Gather(
            inner_worker(start),
            inner_worker(start + 1),
        )
        return results

    @do
    def nested_gather() -> EffectGenerator[list[list[int]]]:
        results = yield Gather(
            outer_worker(0),
            outer_worker(10),
        )
        return results

    result = await engine.run_async(nested_gather())

    assert result.is_ok
    assert result.value == [[0, 1], [10, 11]]


@pytest.mark.asyncio
async def test_gather_preserves_order(interpreter: "Interpreter") -> None:
    """Test Gather preserves program order in results."""
    engine = interpreter

    @do
    def delayed_worker(delay_ms: int, value: str) -> EffectGenerator[str]:
        yield Await(asyncio.sleep(delay_ms / 1000))
        yield Log(f"Done: {value}")
        return value

    @do
    def order_test() -> EffectGenerator[list[str]]:
        # Different delays, but results should be in program order
        results = yield Gather(
            delayed_worker(30, "first"),
            delayed_worker(10, "second"),
            delayed_worker(20, "third"),
        )
        return results

    result = await engine.run_async(order_test())

    assert result.is_ok
    # Gather is sequential in CESK, so order is preserved
    assert result.value == ["first", "second", "third"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
