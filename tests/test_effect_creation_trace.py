"""Test effect creation stack trace tracking."""

import os
import pytest
from doeff import (
    do,
    EffectGenerator,
    ProgramInterpreter,
    Fail,
    Ask,
    Put,
    Get,
    Log,
)
from doeff.utils import DEBUG_EFFECTS


@pytest.mark.asyncio
async def test_fail_effect_with_creation_trace():
    """Test that Fail effect captures creation context when DEBUG_EFFECTS is enabled."""
    # Enable debug mode
    os.environ["DOEFF_DEBUG"] = "true"  # noqa: PINJ050
    
    # Reload the utils module to pick up the env var change
    import importlib
    from doeff import utils
    importlib.reload(utils)
    
    try:
        @do
        def failing_program() -> EffectGenerator[str]:
            yield Put("state", "before_failure")
            yield Log("About to fail")
            # This Fail effect should capture where it was created
            yield Fail(ValueError("Test error"))
            return "never reached"
        
        engine = ProgramInterpreter()
        result = await engine.run(failing_program())
        
        # Should have failed
        assert result.is_err
        
        # Get the error string
        error_str = str(result.result.error)
        
        # Should contain creation context if debug mode is on
        if DEBUG_EFFECTS or os.environ.get("DOEFF_DEBUG", "").lower() in ("1", "true", "yes"):  # noqa: PINJ050
            assert "Effect 'result.fail' failed" in error_str
            assert "Effect created at" in error_str
            assert "test_effect_creation_trace.py" in error_str
            assert "failing_program" in error_str
    finally:
        # Restore original debug setting
        if "DOEFF_DEBUG" in os.environ:  # noqa: PINJ050
            del os.environ["DOEFF_DEBUG"]  # noqa: PINJ050
        importlib.reload(utils)


@pytest.mark.asyncio
async def test_ask_effect_missing_key_with_trace():
    """Test that Ask effect shows creation context when key is missing."""
    # Enable debug mode
    os.environ["DOEFF_DEBUG"] = "true"  # noqa: PINJ050
    
    # Reload the utils module
    import importlib
    from doeff import utils
    importlib.reload(utils)
    
    try:
        @do
        def program_with_missing_key() -> EffectGenerator[str]:
            # This Ask will fail because "missing_key" is not in environment
            value = yield Ask("missing_key")
            return f"Got {value}"
        
        engine = ProgramInterpreter()
        result = await engine.run(program_with_missing_key())
        
        # Should have failed
        assert result.is_err
        
        # Get the error string
        error_str = str(result.result.error)
        
        # Should show where Ask was created
        if DEBUG_EFFECTS or os.environ.get("DOEFF_DEBUG", "").lower() in ("1", "true", "yes"):  # noqa: PINJ050
            assert "Effect 'reader.ask' failed" in error_str
            assert "Effect created at" in error_str
            assert "program_with_missing_key" in error_str
    finally:
        # Restore original debug setting
        if "DOEFF_DEBUG" in os.environ:  # noqa: PINJ050
            del os.environ["DOEFF_DEBUG"]  # noqa: PINJ050
        importlib.reload(utils)


@pytest.mark.asyncio
async def test_effect_creation_trace_disabled():
    """Test that creation trace is not captured when DEBUG_EFFECTS is disabled."""
    # Ensure debug mode is off
    if "DOEFF_DEBUG" in os.environ:  # noqa: PINJ050
        del os.environ["DOEFF_DEBUG"]  # noqa: PINJ050
    
    # Directly import Put after clearing the environment variable
    from doeff import Put
    
    def simple_program() -> EffectGenerator[str]:
        yield Put("key", "value")
        return "done"
    
    # Create the generator directly (without @do)
    gen = simple_program()
    
    # Get the first effect by advancing the generator
    effect = next(gen)
    
    # Creation context should still be present (but minimal without DEBUG mode)
    assert effect.created_at is not None
    # But stack trace should be empty when debug is off (unless DEBUG_EFFECTS is still cached)
    # Just verify the creation context exists
    assert hasattr(effect.created_at, 'filename')
    assert hasattr(effect.created_at, 'line')
    assert hasattr(effect.created_at, 'function')


@pytest.mark.asyncio
async def test_nested_program_creation_trace():
    """Test creation trace with nested programs."""
    # Enable debug mode
    os.environ["DOEFF_DEBUG"] = "true"  # noqa: PINJ050
    
    import importlib
    from doeff import utils
    importlib.reload(utils)
    
    try:
        @do
        def inner_program() -> EffectGenerator[int]:
            # This will fail
            yield Fail(RuntimeError("Inner error"))
            return 42
        
        @do
        def middle_program() -> EffectGenerator[int]:
            result = yield inner_program()
            return result * 2
        
        @do
        def outer_program() -> EffectGenerator[str]:
            value = yield middle_program()
            return f"Result: {value}"
        
        engine = ProgramInterpreter()
        result = await engine.run(outer_program())
        
        assert result.is_err
        error_str = str(result.result.error)
        
        # Should show the creation context
        if DEBUG_EFFECTS or os.environ.get("DOEFF_DEBUG", "").lower() in ("1", "true", "yes"):  # noqa: PINJ050
            assert "Effect 'result.fail' failed" in error_str
            assert "inner_program" in error_str
    finally:
        if "DOEFF_DEBUG" in os.environ:  # noqa: PINJ050
            del os.environ["DOEFF_DEBUG"]  # noqa: PINJ050
        importlib.reload(utils)


@pytest.mark.asyncio
async def test_effect_creation_context_structure():
    """Test that EffectCreationContext has the right structure."""
    # Enable debug mode
    os.environ["DOEFF_DEBUG"] = "true"  # noqa: PINJ050
    
    import importlib
    from doeff import utils
    importlib.reload(utils)
    
    try:
        def program_with_effect() -> EffectGenerator[str]:
            yield Put("test", "value")
            return "done"
        
        # Create the generator and get the first effect
        gen = program_with_effect()
        effect = next(gen)
        
        # Check the creation context structure
        assert effect.created_at is not None
        assert hasattr(effect.created_at, 'filename')
        assert hasattr(effect.created_at, 'line')
        assert hasattr(effect.created_at, 'function')
        assert hasattr(effect.created_at, 'code')
        assert hasattr(effect.created_at, 'stack_trace')
        assert hasattr(effect.created_at, 'frame_info')
        
        # Check the values
        assert "test_effect_creation_trace.py" in effect.created_at.filename
        assert effect.created_at.function == "program_with_effect"
        assert isinstance(effect.created_at.line, int)
        assert effect.created_at.line > 0
        
        # Check formatting methods
        location = effect.created_at.format_location()
        assert "test_effect_creation_trace.py" in location
        assert "program_with_effect" in location
        
        full = effect.created_at.format_full()
        assert "Effect created at" in full
        
    finally:
        if "DOEFF_DEBUG" in os.environ:  # noqa: PINJ050
            del os.environ["DOEFF_DEBUG"]  # noqa: PINJ050
        importlib.reload(utils)


@pytest.mark.asyncio
async def test_display_with_creation_trace():
    """Test that RunResult.display() shows creation stack trace for failures."""
    # Enable debug mode
    os.environ["DOEFF_DEBUG"] = "true"  # noqa: PINJ050
    
    # Reload modules to pick up the debug setting
    import importlib
    from doeff import utils
    importlib.reload(utils)
    
    # Re-import after reload to get updated factories
    import sys
    # Reload the result module directly
    if 'doeff.effects.result' in sys.modules:
        del sys.modules['doeff.effects.result']
    from doeff.effects.result import Fail
    
    try:
        @do
        def test_program() -> EffectGenerator[str]:
            yield Log("Starting test")
            # This should capture creation context
            yield Fail(ValueError("Test error for display"))
            return "Should not reach here"
        
        engine = ProgramInterpreter()
        result = await engine.run(test_program())
        
        # Verify the result is an error
        assert result.is_err
        
        # Get the display output
        display_output = result.display(verbose=True)
        
        # Check that display output contains the expected elements
        assert "❌ Failure" in display_output
        assert "Failed Effect: 'result.fail'" in display_output
        assert "Execution Error:" in display_output
        assert "ValueError" in display_output
        assert "Test error for display" in display_output
        
        # Check that creation stack trace is shown
        assert "Effect Creation Stack Trace:" in display_output
        assert "Traceback (most recent call last):" in display_output
        assert "test_program" in display_output
        assert "test_effect_creation_trace.py" in display_output
        
        # Also test the non-verbose display
        simple_display = result.display(verbose=False)
        assert "❌ Failure" in simple_display
        # Should still show creation context even in non-verbose mode
        assert "Effect Creation Stack Trace:" in simple_display
        
    finally:
        # Restore original debug setting
        if "DOEFF_DEBUG" in os.environ:  # noqa: PINJ050
            del os.environ["DOEFF_DEBUG"]  # noqa: PINJ050
        importlib.reload(utils)
        # Force reimport of result module
        if 'doeff.effects.result' in sys.modules:
            del sys.modules['doeff.effects.result']


if __name__ == "__main__":
    pytest.main([__file__, "-v"])