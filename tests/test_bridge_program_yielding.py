"""
Test the pinjected bridge for handling yielded Programs.

This test file focuses on verifying that the bridge correctly handles
the case where a Program yields another Program, not just Effects.
"""

import pytest
from typing import Generator, Any, Union
from pinjected import design, AsyncResolver

from doeff import (
    Program,
    do,
    Effect,
    EffectGenerator,
    Log,
    Step,
    Dep,
    Put,
    Get,
)
from doeff_pinjected.bridge import (
    program_to_injected,
    program_to_iproxy,
    program_to_injected_result,
)


@pytest.mark.asyncio
async def test_bridge_handles_yielded_program():  # noqa: PINJ040
    """Test that the bridge correctly handles when a Program yields another Program."""

    @do
    def inner_program(x: int) -> EffectGenerator[int]:
        """An inner program that will be yielded by another."""
        yield Log(f"Inner program processing {x}")
        result = x * 2
        yield Step(result, {"operation": "multiply", "input": x, "output": result})
        return result

    @do
    def outer_program() -> EffectGenerator[int]:
        """An outer program that yields another Program."""
        yield Log("Starting outer program")

        # This is the key test: yielding a Program, not an Effect
        inner_result = yield inner_program(5)

        yield Log(f"Got result from inner: {inner_result}")
        final = inner_result + 10
        yield Step(final, {"operation": "add", "value": final})
        return final

    # Test with program_to_injected
    injected = program_to_injected(outer_program())

    # Create a test resolver with no dependencies
    test_design = design()
    resolver = AsyncResolver(test_design)  # pinjected: allow-async-resolver

    # Run through pinjected
    result = await resolver.provide(injected)

    assert result == 20  # (5 * 2) + 10

    # Also test with program_to_injected_result to verify logs
    injected_result = program_to_injected_result(outer_program())
    run_result = await resolver.provide(injected_result)

    assert run_result.is_ok
    assert run_result.value == 20
    assert "Starting outer program" in run_result.log
    assert "Inner program processing 5" in run_result.log
    assert "Got result from inner: 10" in run_result.log


@pytest.mark.asyncio
async def test_bridge_handles_nested_program_yielding():  # noqa: PINJ040
    """Test deeply nested Program yielding."""

    @do
    def level3(x: int) -> EffectGenerator[int]:
        yield Log(f"Level 3: {x}")
        return x * 3

    @do
    def level2(x: int) -> EffectGenerator[int]:
        yield Log(f"Level 2: {x}")
        # Yield another Program
        result = yield level3(x)
        return result * 2

    @do
    def level1() -> EffectGenerator[int]:
        yield Log("Level 1")
        # Yield a Program that itself yields a Program
        result = yield level2(5)
        return result + 1

    injected = program_to_injected(level1())

    test_design = design()
    resolver = AsyncResolver(test_design)  # pinjected: allow-async-resolver

    result = await resolver.provide(injected)
    assert result == 31  # ((5 * 3) * 2) + 1 = 31


@pytest.mark.asyncio
async def test_bridge_handles_program_with_dependencies():  # noqa: PINJ040
    """Test that both Dep effects and yielded Programs work together."""

    @do
    def compute_with_dep(x: int) -> EffectGenerator[int]:
        """A program that uses a dependency."""
        multiplier = yield Dep("multiplier")
        yield Log(f"Computing {x} * {multiplier}")
        return x * multiplier

    @do
    def main_program() -> EffectGenerator[int]:
        """Main program that yields another Program that has dependencies."""
        yield Log("Main program starting")

        # Yield a Program that needs dependency injection
        result = yield compute_with_dep(7)

        yield Log(f"Got result: {result}")
        return result + 100

    injected = program_to_injected(main_program())

    # Create a resolver with the required dependency
    test_design = design(multiplier=3)
    resolver = AsyncResolver(test_design)  # pinjected: allow-async-resolver

    result = await resolver.provide(injected)
    assert result == 121  # (7 * 3) + 100 = 121

    # Verify with result version
    injected_result = program_to_injected_result(main_program())
    run_result = await resolver.provide(injected_result)

    assert run_result.is_ok
    assert run_result.value == 121
    assert "Main program starting" in run_result.log
    assert "Computing 7 * 3" in run_result.log
    assert "Got result: 21" in run_result.log


@pytest.mark.asyncio
async def test_bridge_handles_program_flat_map():  # noqa: PINJ040
    """Test that flat_map (monadic bind) works through the bridge."""

    @do
    def get_value() -> EffectGenerator[int]:
        yield Log("Getting value")
        return 10

    @do
    def process_value(x: int) -> EffectGenerator[str]:
        yield Log(f"Processing {x}")
        return f"Result: {x * 2}"

    # Use flat_map to chain Programs
    prog = get_value().flat_map(process_value)

    injected = program_to_injected(prog)

    test_design = design()
    resolver = AsyncResolver(test_design)  # pinjected: allow-async-resolver

    result = await resolver.provide(injected)
    assert result == "Result: 20"


@pytest.mark.asyncio
async def test_bridge_handles_pure_program():  # noqa: PINJ040
    """Test that pure Programs (created with Program.pure) work through the bridge."""

    @do
    def program_with_pure() -> EffectGenerator[int]:
        yield Log("Starting")

        # Yield a pure Program
        value = yield Program.pure(42)

        yield Log(f"Got pure value: {value}")
        return value + 8

    injected = program_to_injected(program_with_pure())

    test_design = design()
    resolver = AsyncResolver(test_design)  # pinjected: allow-async-resolver

    result = await resolver.provide(injected)
    assert result == 50


@pytest.mark.asyncio
async def test_bridge_handles_mixed_effects_and_programs():  # noqa: PINJ040
    """Test complex interaction of Effects and yielded Programs."""

    @do
    def helper(name: str) -> EffectGenerator[str]:
        config = yield Dep("config")
        yield Log(f"Helper {name} with config: {config}")
        return f"{name}-{config}"

    @do
    def complex_program() -> EffectGenerator[dict]:
        yield Log("Starting complex program")

        # Mix Effects and Programs
        yield Step("step1", {"phase": "init"})

        # Yield a Program
        result1 = yield helper("first")

        # Another Effect
        yield Log(f"After first helper: {result1}")

        # Another Program
        result2 = yield helper("second")

        # Use Effects.state
        yield Put("key", "value")
        state_val = yield Get("key")

        yield Log(f"State value: {state_val}")

        return {
            "result1": result1,
            "result2": result2,
            "state": state_val,
        }

    injected = program_to_injected(complex_program())

    test_design = design(config="test-env")
    resolver = AsyncResolver(test_design)  # pinjected: allow-async-resolver

    result = await resolver.provide(injected)

    assert result == {
        "result1": "first-test-env",
        "result2": "second-test-env",
        "state": "value",
    }


@pytest.mark.asyncio
async def test_bridge_error_propagation_with_programs():  # noqa: PINJ040
    """Test that errors in yielded Programs propagate correctly through the bridge."""

    @do
    def failing_program() -> EffectGenerator[int]:
        yield Log("About to fail")
        raise ValueError("Intentional error in nested Program")
        return 42  # Never reached

    @do
    def outer_with_error() -> EffectGenerator[int]:
        yield Log("Outer starting")

        # This should propagate the error
        result = yield failing_program()

        yield Log("This should not be logged")
        return result

    injected = program_to_injected(outer_with_error())

    test_design = design()
    resolver = AsyncResolver(test_design)  # pinjected: allow-async-resolver

    # The error should propagate (wrapped in ExceptionGroup by pinjected's TaskGroup)
    with pytest.raises(ExceptionGroup) as exc_info:
        await resolver.provide(injected)
    # Check that the ValueError is in the exception group
    assert len(exc_info.value.exceptions) == 1
    assert isinstance(exc_info.value.exceptions[0], ValueError)
    assert "Intentional error in nested Program" in str(exc_info.value.exceptions[0])

    # With program_to_injected_result, we should get an error result
    injected_result = program_to_injected_result(outer_with_error())
    run_result = await resolver.provide(injected_result)

    assert run_result.is_err
    assert "Intentional error in nested Program" in str(run_result.result.error.exc)
    assert "Outer starting" in run_result.log
    assert "About to fail" in run_result.log
    assert "This should not be logged" not in run_result.log


@pytest.mark.asyncio
async def test_bridge_with_iproxy():  # noqa: PINJ040
    """Test that program_to_iproxy works correctly with yielded Programs."""

    @do
    def inner(x: int) -> EffectGenerator[int]:
        yield Log(f"Inner: {x}")
        return x * x

    @do
    def outer() -> EffectGenerator[int]:
        yield Log("Outer")
        result = yield inner(6)
        return result + 4

    # Convert to IProxy
    proxy = program_to_iproxy(outer())

    # Resolve through pinjected
    test_design = design()
    resolver = AsyncResolver(test_design)  # pinjected: allow-async-resolver

    result = await resolver.provide(proxy)
    assert result == 40  # (6 * 6) + 4 = 40


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
