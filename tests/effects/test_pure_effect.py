"""Tests for PureEffect - the immediate value effect (Pure case of Free monad)."""

import pytest

from doeff import CESKInterpreter, do
from doeff.effects.pure import Pure, PureEffect
from doeff.program import Program
from doeff.types import Ok


@pytest.mark.asyncio
async def test_pure_effect_returns_value():
    """PureEffect should immediately return its wrapped value."""
    interpreter = CESKInterpreter()

    # Create a PureEffect directly
    effect = PureEffect(value=42)

    # Wrap in a program
    @do
    def pure_program() -> Program[int]:
        result = yield effect
        return result

    # Run the program
    run_result = await interpreter.run_async(pure_program())

    # Should succeed with the wrapped value
    assert run_result.result == Ok(42)
    assert run_result.value == 42


@pytest.mark.asyncio
async def test_pure_effect_with_complex_value():
    """PureEffect should work with any value type."""
    interpreter = CESKInterpreter()

    complex_value = {"key": "value", "nested": [1, 2, 3]}
    effect = PureEffect(value=complex_value)

    @do
    def pure_program() -> Program[dict]:
        result = yield effect
        return result

    run_result = await interpreter.run_async(pure_program())

    assert run_result.result == Ok(complex_value)
    assert run_result.value == complex_value


@pytest.mark.asyncio
async def test_pure_effect_with_none():
    """PureEffect should handle None value."""
    interpreter = CESKInterpreter()

    effect = PureEffect(value=None)

    @do
    def pure_program() -> Program[None]:
        result = yield effect
        return result

    run_result = await interpreter.run_async(pure_program())

    assert run_result.result == Ok(None)
    assert run_result.value is None


@pytest.mark.asyncio
async def test_pure_factory_function():
    """Pure() factory should create PureEffect with trace context."""
    interpreter = CESKInterpreter()

    @do
    def pure_program() -> Program[str]:
        result = yield Pure("hello")
        return result

    run_result = await interpreter.run_async(pure_program())

    assert run_result.result == Ok("hello")
    assert run_result.value == "hello"


@pytest.mark.asyncio
async def test_pure_effect_in_composition():
    """PureEffect should compose with other effects."""
    from doeff.effects import Ask
    from doeff.types import ExecutionContext

    interpreter = CESKInterpreter()

    @do
    def composed_program() -> Program[str]:
        # Get value from environment
        name = yield Ask("name")
        # Use Pure to wrap intermediate computation
        greeting = yield Pure(f"Hello, {name}!")
        return greeting

    ctx = ExecutionContext(env={"name": "World"})
    run_result = await interpreter.run_async(composed_program(), ctx)

    assert run_result.result == Ok("Hello, World!")


def test_pure_effect_intercept():
    """PureEffect.intercept should return self (no nested programs)."""
    effect = PureEffect(value=42)

    def dummy_transform(e):
        return e

    # intercept should return self for PureEffect
    intercepted = effect.intercept(dummy_transform)
    assert intercepted is effect


def test_pure_effect_immutable():
    """PureEffect should be frozen/immutable."""
    effect = PureEffect(value=42)

    # Should not be able to modify
    with pytest.raises(AttributeError, match=r"can't set attribute|cannot assign to field"):
        effect.value = 100  # type: ignore
