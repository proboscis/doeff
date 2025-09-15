"""
Test KleisliProgram automatic unwrapping of Program arguments.
"""

import pytest
import asyncio
from typing import Generator, Any, Union

from doeff import (
    Program,
    KleisliProgram,
    do,
    Effect,
    ProgramInterpreter,
    ExecutionContext,
    # Effects
    Put,
    Get,
    Log,
    Await,
)


@pytest.mark.asyncio
async def test_kleisli_basic():
    """Test that @do now returns a KleisliProgram."""

    @do
    def add(x: int, y: int) -> Generator[Union[Effect, Program], Any, int]:
        if False: yield  # Make it a generator
        return x + y

    # Check that add is a KleisliProgram
    assert isinstance(add, KleisliProgram)

    # Calling it should return a Program
    prog = add(2, 3)
    assert isinstance(prog, Program)

    # Run it
    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_ok
    assert result.value == 5


@pytest.mark.asyncio
async def test_kleisli_unwrap_program_args():
    """Test automatic unwrapping of Program arguments."""

    @do
    def add(x: int, y: int) -> Generator[Union[Effect, Program], Any, int]:
        yield Log(f"Adding {x} + {y}")
        return x + y

    # Create Programs for the arguments
    prog_x = Program.pure(10)
    prog_y = Program.pure(20)

    # Call with Program arguments - should unwrap automatically
    prog = add(prog_x, prog_y)

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_ok
    assert result.value == 30
    assert len(result.log) == 1
    assert "Adding 10 + 20" in result.log[0]


@pytest.mark.asyncio
async def test_kleisli_mixed_args():
    """Test mixing Program and regular arguments."""

    @do
    def multiply(x: int, y: int, z: int) -> Generator[Union[Effect, Program], Any, int]:
        yield Log(f"Multiplying {x} * {y} * {z}")
        return x * y * z

    prog_x = Program.pure(2)
    # y is a regular value
    prog_z = Program.pure(5)

    # Mix Program and regular arguments
    prog = multiply(prog_x, 3, prog_z)

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_ok
    assert result.value == 30  # 2 * 3 * 5
    assert "Multiplying 2 * 3 * 5" in result.log[0]


@pytest.mark.asyncio
async def test_kleisli_kwargs():
    """Test unwrapping Program arguments passed as kwargs."""

    @do
    def greet(
        name: str, age: int, city: str
    ) -> Generator[Union[Effect, Program], Any, str]:
        yield Log(f"Creating greeting for {name}")
        return f"{name} is {age} years old and lives in {city}"

    prog_name = Program.pure("Alice")
    prog_age = Program.pure(30)

    # Use kwargs with mixed Program and regular values
    prog = greet(name=prog_name, age=prog_age, city="Tokyo")

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_ok
    assert result.value == "Alice is 30 years old and lives in Tokyo"
    assert "Creating greeting for Alice" in result.log[0]


@pytest.mark.asyncio
async def test_kleisli_with_effects():
    """Test KleisliProgram with functions that use effects."""

    @do
    def fetch_value(key: str) -> Generator[Union[Effect, Program], Any, int]:
        value = yield Get(key)
        if value is None:
            value = 0
        yield Log(f"Fetched {key}={value}")
        return value

    @do
    def compute(x: int, y: int) -> Generator[Union[Effect, Program], Any, int]:
        yield Put("x", x)
        yield Put("y", y)
        sum_val = x + y
        yield Put("sum", sum_val)
        yield Log(f"Computed {x} + {y} = {sum_val}")
        return sum_val

    # Create Programs that use effects
    prog_x = fetch_value("input_x")
    prog_y = fetch_value("input_y")

    # Pass them to compute - should unwrap automatically
    prog = compute(prog_x, prog_y)

    engine = ProgramInterpreter()
    context = ExecutionContext(state={"input_x": 5, "input_y": 7})
    result = await engine.run(prog, context)

    assert result.is_ok
    assert result.value == 12
    assert result.state["sum"] == 12
    assert len(result.log) == 3  # Two fetches and one compute


@pytest.mark.asyncio
async def test_kleisli_composition():
    """Test composing KleisliPrograms."""

    @do
    def double(x: int) -> Generator[Union[Effect, Program], Any, int]:
        yield Log(f"Doubling {x}")
        return x * 2

    @do
    def add_ten(x: int) -> Generator[Union[Effect, Program], Any, int]:
        yield Log(f"Adding 10 to {x}")
        return x + 10

    @do
    def stringify(x: int) -> Generator[Union[Effect, Program], Any, str]:
        yield Log(f"Converting {x} to string")
        return f"Result: {x}"

    # Compose by passing Programs to each other
    prog_val = Program.pure(5)
    prog_doubled = double(prog_val)  # Returns Program[int]
    prog_added = add_ten(prog_doubled)  # Unwraps prog_doubled automatically
    prog_final = stringify(prog_added)  # Unwraps prog_added automatically

    engine = ProgramInterpreter()
    result = await engine.run(prog_final)

    assert result.is_ok
    assert result.value == "Result: 20"  # (5 * 2) + 10 = 20
    assert len(result.log) == 3


@pytest.mark.asyncio
async def test_kleisli_async():
    """Test KleisliProgram with async effects."""

    async def async_fetch(url: str) -> str:
        await asyncio.sleep(0.01)
        return f"Data from {url}"

    @do
    def fetch_data(url: str) -> Generator[Union[Effect, Program], Any, str]:
        yield Log(f"Fetching {url}")
        data = yield Await(async_fetch(url))
        return data

    @do
    def process_data(
        data: str, prefix: str
    ) -> Generator[Union[Effect, Program], Any, str]:
        yield Log(f"Processing data with prefix {prefix}")
        return f"{prefix}: {data}"

    # Create Program that fetches data
    prog_data = fetch_data("https://api.example.com")
    prog_prefix = Program.pure("PROCESSED")

    # Process with Program arguments
    prog_result = process_data(prog_data, prog_prefix)

    engine = ProgramInterpreter()
    result = await engine.run(prog_result)

    assert result.is_ok
    assert result.value == "PROCESSED: Data from https://api.example.com"
    assert len(result.log) == 2


@pytest.mark.asyncio
async def test_kleisli_no_args():
    """Test KleisliProgram with no arguments."""

    @do
    def get_constant() -> Generator[Union[Effect, Program], Any, int]:
        yield Log("Getting constant value")
        return 42

    # Should still be a KleisliProgram
    assert isinstance(get_constant, KleisliProgram)

    # Call with no args
    prog = get_constant()
    assert isinstance(prog, Program)

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_ok
    assert result.value == 42


@pytest.mark.asyncio
async def test_kleisli_error_propagation():
    """Test that errors propagate through KleisliProgram unwrapping."""

    @do
    def failing_prog() -> Generator[Union[Effect, Program], Any, int]:
        yield Log("About to fail")
        raise ValueError("Intentional error")
        return 42  # Never reached

    @do
    def use_value(x: int) -> Generator[Union[Effect, Program], Any, str]:
        yield Log(f"Using value {x}")
        return f"Value: {x}"

    prog_fail = failing_prog()
    prog_result = use_value(prog_fail)  # Should propagate the error

    engine = ProgramInterpreter()
    result = await engine.run(prog_result)

    assert result.is_err
    assert "Intentional error" in str(result.result.error.exc)
    # Only the first log should be there, not "Using value"
    assert len(result.log) == 1
    assert result.log[0] == "About to fail"


@pytest.mark.asyncio
async def test_kleisli_all_program_args():
    """Test calling KleisliProgram with all arguments being Programs."""

    @do
    def concat_three(
        a: str, b: str, c: str
    ) -> Generator[Union[Effect, Program], Any, str]:
        yield Log(f"Concatenating {a}, {b}, {c}")
        return f"{a}-{b}-{c}"

    prog_a = Program.pure("foo")
    prog_b = Program.pure("bar")
    prog_c = Program.pure("baz")

    # All args are Programs
    prog = concat_three(prog_a, prog_b, prog_c)

    engine = ProgramInterpreter()
    result = await engine.run(prog)

    assert result.is_ok
    assert result.value == "foo-bar-baz"


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
