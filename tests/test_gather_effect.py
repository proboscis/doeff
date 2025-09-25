"""
Test the Gather effect for collecting multiple Programs.
"""

import asyncio
from collections.abc import Generator
from typing import Any

import pytest

from doeff import (
    Await,
    Effect,
    Gather,
    GatherDict,
    Get,
    Local,
    Log,
    Program,
    ProgramInterpreter,
    Put,
    do,
)


@pytest.mark.asyncio
async def test_gather_list():
    """Test Gather effect with a list of Programs."""

    @do
    def make_value(x: int) -> Generator[Effect | Program, Any, int]:
        yield Log(f"Making value {x}")
        return x * 2

    @do
    def gather_test() -> Generator[Effect | Program, Any, list]:
        # Create multiple programs
        programs = [make_value(1), make_value(2), make_value(3)]

        # Gather all their results at once
        results = yield Gather(*programs)

        yield Log(f"Gathered results: {results}")
        return results

    prog = gather_test()

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_ok
    assert result.value == [2, 4, 6]
    # Should have 3 logs from make_value + 1 from gather_test
    assert len(result.log) == 4
    assert "Making value 1" in result.log[0]
    assert "Making value 2" in result.log[1]
    assert "Making value 3" in result.log[2]
    assert "Gathered results: [2, 4, 6]" in result.log[3]


@pytest.mark.asyncio
async def test_gather_dict():
    """Test GatherDict effect with a dict of Programs."""

    @do
    def compute(x: int) -> Generator[Effect | Program, Any, int]:
        yield Log(f"Computing with {x}")
        return x**2

    @do
    def gather_dict_test() -> Generator[Effect | Program, Any, dict]:
        # Create a dict of programs
        programs = {
            "first": compute(2),
            "second": compute(3),
            "third": compute(4),
        }

        # Gather all their results at once
        results = yield GatherDict(programs)

        yield Log(f"Gathered dict: {results}")
        return results

    prog = gather_dict_test()

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_ok
    assert result.value == {"first": 4, "second": 9, "third": 16}
    assert len(result.log) == 4  # 3 computes + 1 final log


@pytest.mark.asyncio
async def test_gather_with_state():
    """Test that Gather runs Programs in parallel with isolated initial state."""

    @do
    def increment_counter() -> Generator[Effect | Program, Any, int]:
        counter = yield Get("counter")
        if counter is None:
            counter = 0
        counter += 1
        yield Put("counter", counter)
        yield Log(f"Counter is now {counter}")
        return counter

    @do
    def gather_state_test() -> Generator[Effect | Program, Any, list]:
        # Create multiple programs that modify state
        programs = [increment_counter() for _ in range(3)]

        # Gather runs them in parallel - each starts with same initial state
        results = yield Gather(*programs)

        final_counter = yield Get("counter")
        yield Log(f"Final counter: {final_counter}")
        return results

    prog = gather_state_test()

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_ok
    # In parallel execution, all programs start with counter=None/0, so all return 1
    assert result.value == [1, 1, 1]
    # Final state has counter=1 (last update wins in parallel merge)
    assert result.state["counter"] == 1
    assert len(result.log) == 4  # 3 increments + 1 final


@pytest.mark.asyncio
async def test_gather_empty():
    """Test Gather with empty list."""

    @do
    def empty_gather() -> Generator[Effect | Program, Any, list]:
        results = yield Gather()
        return results

    prog = empty_gather()

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_ok
    assert result.value == []


@pytest.mark.asyncio
async def test_gather_dict_empty():
    """Test GatherDict with empty dict."""

    @do
    def empty_gather_dict() -> Generator[Effect | Program, Any, dict]:
        results = yield GatherDict({})
        return results

    prog = empty_gather_dict()

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_ok
    assert result.value == {}


@pytest.mark.asyncio
async def test_gather_with_async():
    """Test Gather with async operations."""

    async def fetch_data(id: int) -> str:
        await asyncio.sleep(0.01)
        return f"Data-{id}"

    @do
    def async_prog(id: int) -> Generator[Effect | Program, Any, str]:
        data = yield Await(fetch_data(id))
        yield Log(f"Fetched: {data}")
        return data

    @do
    def gather_async_test() -> Generator[Effect | Program, Any, list]:
        programs = [async_prog(i) for i in range(3)]
        results = yield Gather(*programs)
        return results

    prog = gather_async_test()

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_ok
    assert result.value == ["Data-0", "Data-1", "Data-2"]
    assert len(result.log) == 3


@pytest.mark.asyncio
async def test_gather_error_propagation():
    """Test that errors in gathered Programs propagate correctly."""

    @do
    def good_prog(x: int) -> Generator[Effect | Program, Any, int]:
        yield Log(f"Good prog {x}")
        return x

    @do
    def bad_prog() -> Generator[Effect | Program, Any, int]:
        yield Log("About to fail")
        raise ValueError("Intentional error")
        return 42  # Never reached

    @do
    def gather_with_error() -> Generator[Effect | Program, Any, list]:
        programs = [good_prog(1), bad_prog(), good_prog(2)]
        results = yield Gather(*programs)  # Should fail here
        yield Log("This should not be logged")
        return results

    prog = gather_with_error()

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_err
    # Unwrap EffectFailure if needed
    error = result.result.error
    from doeff.types import EffectFailure
    if isinstance(error, EffectFailure):
        error = error.cause
    assert "Intentional error" in str(error)
    # With parallel execution, logs may vary depending on execution order
    # We should have at least 1 log (could be from good_prog(1), bad_prog, or good_prog(2))
    assert len(result.log) >= 1


@pytest.mark.asyncio
async def test_gather_failure_preserves_state():
    """State written inside Local before failure should be visible on parent context."""

    @do
    def scoped_failure() -> Generator[Effect | Program, Any, None]:
        yield Put("branch", "pre-failure")
        raise ValueError("scoped boom")

    @do
    def failing_prog() -> Generator[Effect | Program, Any, None]:
        # Local modifies env but must not isolate state mutations
        yield Local({"env": "scoped"}, scoped_failure())
        return None

    @do
    def successful_prog() -> Generator[Effect | Program, Any, str]:
        yield Put("successful", "ok")
        return "ok"

    @do
    def gather_with_state_failure() -> Generator[Effect | Program, Any, list[Any]]:
        results = yield Gather(successful_prog(), failing_prog())
        return results

    engine = ProgramInterpreter()
    result = await engine.run(gather_with_state_failure())

    assert result.is_err
    assert result.state["successful"] == "ok"
    assert result.state["branch"] == "pre-failure"


@pytest.mark.asyncio
async def test_gather_pure_programs():
    """Test Gather with pure Programs (no effects)."""

    @do
    def gather_pure() -> Generator[Effect | Program, Any, list]:
        programs = [
            Program.pure(10),
            Program.pure(20),
            Program.pure(30),
        ]
        results = yield Gather(*programs)
        return results

    prog = gather_pure()

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_ok
    assert result.value == [10, 20, 30]
    assert len(result.log) == 0  # Pure programs don't log


@pytest.mark.asyncio
async def test_gather_dict_mixed():
    """Test GatherDict with mixed Program types."""

    @do
    def with_effect(x: int) -> Generator[Effect | Program, Any, int]:
        yield Put(f"value_{x}", x)
        yield Log(f"Processing {x}")
        return x * 10

    @do
    def gather_mixed() -> Generator[Effect | Program, Any, dict]:
        programs = {
            "pure": Program.pure(5),
            "effect": with_effect(3),
            "another_pure": Program.pure("hello"),
        }
        results = yield GatherDict(programs)
        return results

    prog = gather_mixed()

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_ok
    assert result.value == {"pure": 5, "effect": 30, "another_pure": "hello"}
    assert result.state["value_3"] == 3
    assert len(result.log) == 1
    assert "Processing 3" in result.log[0]


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
