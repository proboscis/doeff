"""Test to ensure passing Effect->Program function to intercept doesn't cause recursion errors."""

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from doeff import ExecutionContext, Program, ProgramInterpreter, do
from doeff.effects import WriterTellEffect
from doeff.types import Effect, EffectBase, EffectGenerator


@dataclass(frozen=True)
class CustomEffect(EffectBase):
    """Custom effect for testing."""

    value: str

    def intercept(
        self,
        _transform: Callable[[Effect], Effect | Program],
    ) -> "CustomEffect":
        return self


@pytest.mark.asyncio
async def test_intercept_with_effect_to_program_no_recursion():
    """Test that passing Effect->Program function to intercept doesn't cause recursion."""

    # Track interceptor calls to verify it's being called
    interceptor_calls = []

    @do
    def interceptor_effect_to_program(effect: Effect) -> EffectGenerator[Effect]:
        """Interceptor that returns a Program (generator) instead of an Effect."""
        interceptor_calls.append(effect)

        # This is the key test case: returning a Program instead of an Effect
        # The fix should prevent infinite recursion by NOT recursively intercepting
        # the returned Program
        if isinstance(effect, CustomEffect):
            # Return a Program that yields a WriterTellEffect
            yield WriterTellEffect(f"custom_{effect.value}")
            return WriterTellEffect(f"handled_{effect.value}")

        # For other effects, just yield them unchanged
        yield effect
        return effect

    @do
    def main_program() -> EffectGenerator[None]:
        """Main program that uses custom effects."""
        # Use custom effect that will be intercepted
        yield CustomEffect("test1")
        yield CustomEffect("test2")

        # Also use standard effect
        yield WriterTellEffect("log message")

        return None

    # Create interpreter and context
    interpreter = ProgramInterpreter()
    context = ExecutionContext()

    # Apply the interceptor to the program
    # After the fix, this should NOT cause recursion
    intercepted_program = main_program().intercept(lambda e: interceptor_effect_to_program(e))

    # Run the intercepted program
    result = await interpreter.run_async(intercepted_program, context)

    # Verify the program ran successfully without recursion errors
    assert result.is_ok(), f"Program failed with error: {result.result}"
    assert len(interceptor_calls) > 0, "Interceptor was not called"

    # Check that interceptor was called for each effect
    custom_effects = [e for e in interceptor_calls if isinstance(e, CustomEffect)]
    assert len(custom_effects) == 2, f"Expected 2 CustomEffects, got {len(custom_effects)}"

    # Verify the log contains the transformed messages
    assert len(context.log) >= 3, f"Expected at least 3 log entries, got {len(context.log)}"

    # Check specific transformations happened
    log_str = " ".join(str(entry) for entry in context.log)
    assert "custom_test1" in log_str, "First custom effect not transformed correctly"
    assert "custom_test2" in log_str, "Second custom effect not transformed correctly"


@pytest.mark.asyncio
async def test_intercept_with_nested_intercept_no_recursion():
    """Test nested intercept calls don't cause recursion."""

    intercept_level1_calls = []
    intercept_level2_calls = []

    def level1_interceptor(effect: Effect) -> Effect:
        """First level interceptor."""
        intercept_level1_calls.append(effect)
        if isinstance(effect, CustomEffect):
            return CustomEffect(value=effect.value + "_L1")
        return effect

    @do
    def level2_interceptor_program(effect: Effect) -> EffectGenerator[Effect]:
        """Second level interceptor that returns a Program."""
        intercept_level2_calls.append(effect)
        if isinstance(effect, CustomEffect):
            # Transform to WriterTellEffect which the interpreter can handle
            yield WriterTellEffect(f"custom_{effect.value}_L2")
            return WriterTellEffect(f"handled_{effect.value}")
        yield effect
        return effect

    @do
    def test_program() -> EffectGenerator[None]:
        """Program with nested effects."""
        yield CustomEffect("nested")
        return None

    interpreter = ProgramInterpreter()
    context = ExecutionContext()

    # Apply nested interceptors
    # First interceptor transforms CustomEffect("nested") -> CustomEffect("nested_L1")
    # Second interceptor transforms CustomEffect("nested_L1") -> Program yielding WriterTellEffect
    program_with_l1 = test_program().intercept(level1_interceptor)
    program_with_both = program_with_l1.intercept(lambda e: level2_interceptor_program(e))

    result = await interpreter.run_async(program_with_both, context)

    # Verify both interceptors were called
    assert len(intercept_level1_calls) > 0, "Level 1 interceptor was not called"
    assert len(intercept_level2_calls) > 0, "Level 2 interceptor was not called"
    assert result.is_ok(), f"Program failed with error: {result.result}"

    # Check the transformation chain
    assert any(e.value == "nested" for e in intercept_level1_calls if isinstance(e, CustomEffect))
    assert any(e.value == "nested_L1" for e in intercept_level2_calls if isinstance(e, CustomEffect))


@pytest.mark.asyncio
async def test_intercept_direct_effect_return():
    """Test normal case where interceptor returns Effect directly."""

    calls = []

    def simple_interceptor(effect: Effect) -> Effect:
        """Simple interceptor that returns Effect directly."""
        calls.append(effect)
        if isinstance(effect, CustomEffect):
            # Transform CustomEffect to WriterTellEffect which can be handled
            return WriterTellEffect(f"custom_{effect.value}_simple")
        return effect

    @do
    def simple_program() -> EffectGenerator[str]:
        result = yield CustomEffect("direct")
        return f"Result: {result}"

    interpreter = ProgramInterpreter()
    context = ExecutionContext()

    # This should work without any issues
    intercepted = simple_program().intercept(simple_interceptor)
    result = await interpreter.run_async(intercepted, context)

    # Since WriterTellEffect doesn't return a value, result will be None
    assert result.is_ok
    assert result.value == "Result: None"
    assert len(calls) == 1

    # Check that CustomEffect was intercepted
    assert any(isinstance(e, CustomEffect) for e in calls)
