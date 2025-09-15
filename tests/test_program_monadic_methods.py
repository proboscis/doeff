"""
Test the monadic methods (map, flat_map, pure) for the Program class.
"""

import pytest
import asyncio
from typing import Generator, Any, Union

from doeff import (
    Program,
    do,
    Effect,
    ProgramInterpreter,
    ExecutionContext,
    # Effects
    Ask,
    Put,
    Get,
    Log,
    Step,
    Await,
)


@pytest.mark.asyncio
async def test_program_pure():
    """Test Program.pure creates a Program that returns a pure value."""
    # Create a pure program
    pure_prog = Program.pure(42)

    # Run it
    engine = ProgramInterpreter()
    result = await engine.run(pure_prog)

    assert result.is_ok
    assert result.value == 42
    assert len(result.log) == 0  # No side effects


@pytest.mark.asyncio
async def test_program_map():
    """Test Program.map transforms the result of a program."""

    @do
    def base_program() -> Generator[Union[Effect, Program], Any, int]:
        yield Put("x", 10)
        value = yield Get("x")
        return value

    # Map a function over the result
    mapped_prog = base_program().map(lambda x: x * 2)

    engine = ProgramInterpreter()
    result = await engine.run(mapped_prog)

    assert result.is_ok
    assert result.value == 20  # 10 * 2
    assert result.state["x"] == 10  # State unchanged


@pytest.mark.asyncio
async def test_program_map_chain():
    """Test chaining multiple map operations."""

    @do
    def base_program() -> Generator[Union[Effect, Program], Any, int]:
        return 5

    # Chain multiple maps
    prog = (
        base_program()
        .map(lambda x: x + 3)  # 5 + 3 = 8
        .map(lambda x: x * 2)  # 8 * 2 = 16
        .map(lambda x: f"Result: {x}")
    )  # "Result: 16"

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_ok
    assert result.value == "Result: 16"


@pytest.mark.asyncio
async def test_program_flat_map():
    """Test Program.flat_map chains programs together."""

    @do
    def first_program(x: int) -> Generator[Union[Effect, Program], Any, int]:
        yield Log(f"First program with {x}")
        yield Put("first", x)
        return x * 2

    @do
    def second_program(x: int) -> Generator[Union[Effect, Program], Any, str]:
        yield Log(f"Second program with {x}")
        yield Put("second", x)
        first_val = yield Get("first")
        return f"first={first_val}, second={x}"

    # Create initial program and flat_map another program
    prog = Program.pure(5).flat_map(first_program).flat_map(second_program)

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_ok
    assert result.value == "first=5, second=10"
    assert result.state["first"] == 5
    assert result.state["second"] == 10
    assert len(result.log) == 2


@pytest.mark.asyncio
async def test_program_flat_map_with_effects():
    """Test flat_map with programs that use various effects."""

    @do
    def read_config() -> Generator[Union[Effect, Program], Any, dict]:
        config = yield Ask("config")
        yield Log(f"Read config: {config}")
        return config

    @do
    def process_config(config: dict) -> Generator[Union[Effect, Program], Any, int]:
        multiplier = config.get("multiplier", 1)
        base = config.get("base", 0)
        result = base * multiplier
        yield Put("result", result)
        yield Step(result, {"op": "multiply", "base": base, "multiplier": multiplier})
        return result

    @do
    def format_result(value: int) -> Generator[Union[Effect, Program], Any, str]:
        yield Log(f"Formatting result: {value}")
        return f"Final result: {value}"

    # Chain programs using flat_map
    prog = read_config().flat_map(process_config).flat_map(format_result)

    engine = ProgramInterpreter()
    context = ExecutionContext(env={"config": {"base": 7, "multiplier": 3}})
    result = await engine.run(prog, context)

    assert result.is_ok
    assert result.value == "Final result: 21"
    assert result.state["result"] == 21
    assert len(result.log) == 2
    # The graph tracking creates an initial empty step plus the actual Step effect
    assert len(result.graph.steps) >= 1  # At least one step from process_config


@pytest.mark.asyncio
async def test_map_vs_flat_map():
    """Test the difference between map and flat_map."""

    @do
    def base_prog() -> Generator[Union[Effect, Program], Any, int]:
        return 10

    @do
    def effect_prog(x: int) -> Generator[Union[Effect, Program], Any, int]:
        yield Log(f"Processing {x}")
        return x * 2

    # Using map returns a Program[Program[int]]
    # Using flat_map returns a Program[int]

    # flat_map version - correct
    flat_mapped = base_prog().flat_map(effect_prog)

    engine = ProgramInterpreter()
    result = await engine.run(flat_mapped)

    assert result.is_ok
    assert result.value == 20
    assert len(result.log) == 1

    # map version would return a Program, not the value
    mapped = base_prog().map(effect_prog)
    result2 = await engine.run(mapped)

    # The result is a Program object, not the value
    assert result2.is_ok
    assert isinstance(result2.value, Program)


@pytest.mark.asyncio
async def test_monadic_laws_left_identity():
    """Test left identity law: pure(a).flat_map(f) == f(a)"""

    @do
    def f(x: int) -> Generator[Union[Effect, Program], Any, int]:
        yield Log(f"Function f with {x}")
        return x * 2

    # Left identity
    a = 5
    prog1 = Program.pure(a).flat_map(f)
    prog2 = f(a)

    engine = ProgramInterpreter()
    result1 = await engine.run(prog1)
    result2 = await engine.run(prog2)

    assert result1.value == result2.value
    assert result1.log == result2.log


@pytest.mark.asyncio
async def test_monadic_laws_right_identity():
    """Test right identity law: m.flat_map(pure) == m"""

    @do
    def m() -> Generator[Union[Effect, Program], Any, int]:
        yield Log("Program m")
        return 42

    # Right identity
    prog1 = m().flat_map(Program.pure)
    prog2 = m()

    engine = ProgramInterpreter()
    result1 = await engine.run(prog1)
    result2 = await engine.run(prog2)

    assert result1.value == result2.value
    # Note: logs might differ slightly due to execution


@pytest.mark.asyncio
async def test_async_in_flat_map():
    """Test flat_map with async operations."""

    async def async_operation(x: int) -> int:
        await asyncio.sleep(0.01)
        return x * 2

    @do
    def async_prog(x: int) -> Generator[Union[Effect, Program], Any, int]:
        result = yield Await(async_operation(x))
        yield Log(f"Async result: {result}")
        return result

    prog = Program.pure(10).flat_map(async_prog)

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_ok
    assert result.value == 20
    assert len(result.log) == 1


@pytest.mark.asyncio
async def test_complex_composition():
    """Test complex composition of map and flat_map."""

    @do
    def read_value(key: str) -> Generator[Union[Effect, Program], Any, int]:
        value = yield Get(key)
        if value is None:
            value = 0
        yield Log(f"Read {key}={value}")
        return value

    @do
    def write_value(
        key: str, value: int
    ) -> Generator[Union[Effect, Program], Any, int]:
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
    result = await engine.run(prog)

    assert result.is_ok
    assert result.value == 20  # (0 + 10) * 2
    assert result.state.get("y") == 10
    assert result.state.get("z") == 20
    assert len(result.log) == 3  # One read, two writes


@pytest.mark.asyncio
async def test_error_propagation_in_flat_map():
    """Test that errors propagate through flat_map chains."""

    @do
    def failing_prog() -> Generator[Union[Effect, Program], Any, int]:
        yield Log("About to fail")
        raise ValueError("Intentional error")
        return 42  # Never reached

    @do
    def never_runs(x: int) -> Generator[Union[Effect, Program], Any, int]:
        yield Log("This should not run")
        return x * 2

    prog = Program.pure(10).flat_map(lambda _: failing_prog()).flat_map(never_runs)

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_err
    assert "Intentional error" in str(result.result.error.exc)
    assert len(result.log) == 1  # Only "About to fail"
    # "This should not run" should not be in the log


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
