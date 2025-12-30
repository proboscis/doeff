"""
Test the monadic methods (map, flat_map, pure) for the Program class.
"""

import asyncio
from collections.abc import Generator
from typing import Any

import pytest

from doeff import (
    NOTHING,
    # Effects
    Ask,
    AtomicUpdate,
    Await,
    Effect,
    ExecutionContext,
    Gather,
    Get,
    Log,
    Maybe,
    Program,
    ProgramInterpreter,
    Put,
    Some,
    Step,
    do,
)


@pytest.mark.asyncio
async def test_program_pure() -> None:
    """Test Program.pure creates a Program that returns a pure value."""
    # Create a pure program
    pure_prog = Program.pure(42)

    # Run it
    engine = ProgramInterpreter()
    result = await engine.run_async(pure_prog)

    assert result.is_ok
    assert result.value == 42
    assert len(result.log) == 0  # No side effects


@pytest.mark.asyncio
async def test_program_map() -> None:
    """Test Program.map transforms the result of a program."""

    @do
    def base_program() -> Generator[Effect | Program, Any, int]:
        yield Put("x", 10)
        value = yield Get("x")
        return value

    # Map a function over the result
    mapped_prog = base_program().map(lambda x: x * 2)

    engine = ProgramInterpreter()
    result = await engine.run_async(mapped_prog)

    assert result.is_ok
    assert result.value == 20  # 10 * 2
    assert result.state["x"] == 10  # State unchanged


@pytest.mark.asyncio
async def test_program_map_chain() -> None:
    """Test chaining multiple map operations."""

    @do
    def base_program() -> Generator[Effect | Program, Any, int]:
        return 5

    # Chain multiple maps
    prog = (
        base_program()
        .map(lambda x: x + 3)  # 5 + 3 = 8
        .map(lambda x: x * 2)  # 8 * 2 = 16
        .map(lambda x: f"Result: {x}")
    )  # "Result: 16"

    engine = ProgramInterpreter()
    result = await engine.run_async(prog)

    assert result.is_ok
    assert result.value == "Result: 16"


@pytest.mark.asyncio
async def test_program_flat_map() -> None:
    """Test Program.flat_map chains programs together."""

    @do
    def first_program(x: int) -> Generator[Effect | Program, Any, int]:
        yield Log(f"First program with {x}")
        yield Put("first", x)
        return x * 2

    @do
    def second_program(x: int) -> Generator[Effect | Program, Any, str]:
        yield Log(f"Second program with {x}")
        yield Put("second", x)
        first_val = yield Get("first")
        return f"first={first_val}, second={x}"

    # Create initial program and flat_map another program
    prog = Program.pure(5).flat_map(first_program).flat_map(second_program)

    engine = ProgramInterpreter()
    result = await engine.run_async(prog)

    assert result.is_ok
    assert result.value == "first=5, second=10"
    assert result.state["first"] == 5
    assert result.state["second"] == 10
    assert len(result.log) == 2


@pytest.mark.asyncio
async def test_program_flat_map_with_effects() -> None:
    """Test flat_map with programs that use various effects."""

    @do
    def read_config() -> Generator[Effect | Program, Any, dict]:
        config = yield Ask("config")
        yield Log(f"Read config: {config}")
        return config

    @do
    def process_config(config: dict) -> Generator[Effect | Program, Any, int]:
        multiplier = config.get("multiplier", 1)
        base = config.get("base", 0)
        result = base * multiplier
        yield Put("result", result)
        yield Step(result, {"op": "multiply", "base": base, "multiplier": multiplier})
        return result

    @do
    def format_result(value: int) -> Generator[Effect | Program, Any, str]:
        yield Log(f"Formatting result: {value}")
        return f"Final result: {value}"

    # Chain programs using flat_map
    prog = read_config().flat_map(process_config).flat_map(format_result)

    engine = ProgramInterpreter()
    context = ExecutionContext(env={"config": {"base": 7, "multiplier": 3}})
    result = await engine.run_async(prog, context)

    assert result.is_ok
    assert result.value == "Final result: 21"
    assert result.state["result"] == 21
    assert len(result.log) == 2
    # The graph tracking creates an initial empty step plus the actual Step effect
    assert len(result.graph.steps) >= 1  # At least one step from process_config


@pytest.mark.asyncio
async def test_map_vs_flat_map() -> None:
    """Test the difference between map and flat_map."""

    @do
    def base_prog() -> Generator[Effect | Program, Any, int]:
        return 10

    @do
    def effect_prog(x: int) -> Generator[Effect | Program, Any, int]:
        yield Log(f"Processing {x}")
        return x * 2

    # Using map returns a Program[Program[int]]
    # Using flat_map returns a Program[int]

    # flat_map version - correct
    flat_mapped = base_prog().flat_map(effect_prog)

    engine = ProgramInterpreter()
    result = await engine.run_async(flat_mapped)

    assert result.is_ok
    assert result.value == 20
    assert len(result.log) == 1

    # map version would return a Program, not the value
    mapped = base_prog().map(effect_prog)
    result2 = await engine.run_async(mapped)

    # The result is a Program object (or KleisliProgramCall), not the value
    from doeff.program import KleisliProgramCall
    assert result2.is_ok
    assert isinstance(result2.value, (Program, KleisliProgramCall))


@pytest.mark.asyncio
async def test_program_collection_builders_dict() -> None:
    engine = ProgramInterpreter()
    prog = Program.dict({"a": Program.pure(1), "b": 2}, c=Program.pure(3))

    result = await engine.run_async(prog)

    assert result.is_ok
    assert result.value == {"a": 1, "b": 2, "c": 3}


@pytest.mark.asyncio
async def test_program_collection_builders_sequence() -> None:
    engine = ProgramInterpreter()

    list_prog = Program.list(Program.pure(1), 2, Program.pure(3))
    tuple_prog = Program.tuple(Program.pure("x"), "y", Program.pure("z"))
    set_prog = Program.set(Program.pure(1), 2, 2)

    list_result = await engine.run_async(list_prog)
    tuple_result = await engine.run_async(tuple_prog)
    set_result = await engine.run_async(set_prog)

    assert list_result.is_ok
    assert list_result.value == [1, 2, 3]

    assert tuple_result.is_ok
    assert tuple_result.value == ("x", "y", "z")

    assert set_result.is_ok
    assert set_result.value == {1, 2}


@pytest.mark.asyncio
async def test_monadic_laws_left_identity() -> None:
    """Test left identity law: pure(a).flat_map(f) == f(a)"""

    @do
    def f(x: int) -> Generator[Effect | Program, Any, int]:
        yield Log(f"Function f with {x}")
        return x * 2

    # Left identity
    a = 5
    prog1 = Program.pure(a).flat_map(f)
    prog2 = f(a)

    engine = ProgramInterpreter()
    result1 = await engine.run_async(prog1)
    result2 = await engine.run_async(prog2)

    assert result1.value == result2.value
    assert result1.log == result2.log


@pytest.mark.asyncio
async def test_monadic_laws_right_identity() -> None:
    """Test right identity law: m.flat_map(pure) == m"""

    @do
    def m() -> Generator[Effect | Program, Any, int]:
        yield Log("Program m")
        return 42

    # Right identity
    prog1 = m().flat_map(Program.pure)
    prog2 = m()

    engine = ProgramInterpreter()
    result1 = await engine.run_async(prog1)
    result2 = await engine.run_async(prog2)

    assert result1.value == result2.value
    # Note: logs might differ slightly due to execution


@pytest.mark.asyncio
async def test_async_in_flat_map() -> None:
    """Test flat_map with async operations."""

    async def async_operation(x: int) -> int:
        await asyncio.sleep(0.01)
        return x * 2

    @do
    def async_prog(x: int) -> Generator[Effect | Program, Any, int]:
        result = yield Await(async_operation(x))
        yield Log(f"Async result: {result}")
        return result

    prog = Program.pure(10).flat_map(async_prog)

    engine = ProgramInterpreter()
    result = await engine.run_async(prog)

    assert result.is_ok
    assert result.value == 20
    assert len(result.log) == 1


@pytest.mark.asyncio
async def test_complex_composition() -> None:
    """Test complex composition of map and flat_map."""

    @do
    def read_value(key: str) -> Generator[Effect | Program, Any, int]:
        value = yield Get(key)
        if value is None:
            value = 0
        yield Log(f"Read {key}={value}")
        return value

    @do
    def write_value(
        key: str, value: int
    ) -> Generator[Effect | Program, Any, int]:
        yield Put(key, value)
        yield Log(f"Wrote {key}={value}")
        return value

    # Complex composition
    prog = (
        Program.pure("x")
        .flat_map(read_value)  # Read x (0)
        .map(lambda v: v + 10)  # Add 10
        .flat_map(lambda v: write_value("y", v))  # Write to y
        .map(lambda v: v * 2)  # Double
        .flat_map(lambda v: write_value("z", v))
    )  # Write to z

    engine = ProgramInterpreter()
    result = await engine.run_async(prog)

    assert result.is_ok
    assert result.value == 20  # (0 + 10) * 2
    assert result.state.get("y") == 10
    assert result.state.get("z") == 20
    assert len(result.log) == 3  # One read, two writes


@pytest.mark.asyncio
async def test_error_propagation_in_flat_map() -> None:
    """Test that errors propagate through flat_map chains."""

    @do
    def failing_prog() -> Generator[Effect | Program, Any, int]:
        yield Log("About to fail")
        raise ValueError("Intentional error")
        return 42  # Never reached

    @do
    def never_runs(x: int) -> Generator[Effect | Program, Any, int]:
        yield Log("This should not run")
        return x * 2

    prog = Program.pure(10).flat_map(lambda _: failing_prog()).flat_map(never_runs)

    engine = ProgramInterpreter()
    result = await engine.run_async(prog)

    assert result.is_err
    # Unwrap EffectFailure if needed
    error = result.result.error
    from doeff.types import EffectFailure
    if isinstance(error, EffectFailure):
        error = error.cause
    assert "Intentional error" in str(error)
    assert len(result.log) == 1  # Only "About to fail"
    # "This should not run" should not be in the log


@pytest.mark.asyncio
async def test_program_first_success_returns_earliest_success() -> None:
    """first_success should stop at the first program that succeeds."""

    @do
    def failing_one() -> Generator[Effect | Program, Any, int]:
        raise ValueError("boom one")

    @do
    def failing_two() -> Generator[Effect | Program, Any, int]:
        raise RuntimeError("boom two")

    @do
    def succeeding() -> Generator[Effect | Program, Any, int]:
        return 99

    prog = Program.first_success(
        failing_one(),
        failing_two(),
        succeeding(),
    )

    engine = ProgramInterpreter()
    result = await engine.run_async(prog)

    assert result.is_ok
    assert result.value == 99


@pytest.mark.asyncio
async def test_program_first_success_raises_last_error_when_all_fail() -> None:
    """When all candidates fail, first_success should propagate the last error."""

    @do
    def failing_one() -> Generator[Effect | Program, Any, int]:
        raise ValueError("fail first")

    @do
    def failing_two() -> Generator[Effect | Program, Any, int]:
        raise RuntimeError("fail second")

    prog = Program.first_success(failing_one(), failing_two())

    engine = ProgramInterpreter()
    result = await engine.run_async(prog)

    assert result.is_err
    error = result.result.error
    from doeff.types import EffectFailure

    # Unwrap nested EffectFailure wrappers to get to the root cause
    while isinstance(error, EffectFailure):
        error = error.cause

    assert isinstance(error, RuntimeError)
    assert "fail second" in str(error)


@pytest.mark.asyncio
async def test_program_first_some_returns_first_present_value() -> None:
    """first_some should return the first Some value."""

    @do
    def none_program() -> Generator[Effect | Program, Any, Maybe[int]]:
        return Maybe.from_optional(None)

    @do
    def some_program() -> Generator[Effect | Program, Any, Maybe[int]]:
        return Some(7)

    prog = Program.first_some(none_program(), some_program())

    engine = ProgramInterpreter()
    result = await engine.run_async(prog)

    assert result.is_ok
    value = result.value
    assert isinstance(value, Some)
    assert value.unwrap() == 7


@pytest.mark.asyncio
async def test_program_first_some_returns_nothing_when_all_none() -> None:
    """first_some should return Nothing when every program yields None/NOTHING."""

    prog = Program.first_some(
        Program.pure(NOTHING),
        Program.pure(Maybe.from_optional(None)),
    )

    engine = ProgramInterpreter()
    result = await engine.run_async(prog)

    assert result.is_ok
    assert result.value is NOTHING


@pytest.mark.asyncio
async def test_program_first_success_resets_state_between_attempts() -> None:
    """Failed attempts should not mutate shared state for later candidates."""

    @do
    def mutating_failure() -> Generator[Effect | Program, Any, int]:
        yield Put("counter", 99)
        raise RuntimeError("boom")

    @do
    def reader_success() -> Generator[Effect | Program, Any, int]:
        return (yield Get("counter"))

    engine = ProgramInterpreter()
    context = ExecutionContext(state={"counter": 0})

    prog = Program.first_success(mutating_failure(), reader_success())

    result = await engine.run_async(prog, context)

    assert result.is_ok
    assert result.value == 0
    assert context.state["counter"] == 0
    assert context.log == []


@pytest.mark.asyncio
async def test_run_result_display_shows_shared_state() -> None:
    """RunResult.display should surface shared atomic state entries."""

    @do
    def increment_shared() -> Generator[Effect | Program, Any, None]:
        yield AtomicUpdate(
            "shared_counter",
            lambda current: (current or 0) + 1,
            default_factory=lambda: 0,
        )
        return None

    @do
    def run_parallel() -> Generator[Effect | Program, Any, None]:
        yield Gather(increment_shared(), increment_shared())
        return None

    engine = ProgramInterpreter()
    result = await engine.run_async(run_parallel())

    assert result.is_ok
    assert result.state["shared_counter"] == 2

    display = result.display()

    assert "🤝 Shared State:" in display
    assert "shared_counter" in display

if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
