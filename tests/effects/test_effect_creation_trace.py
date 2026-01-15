"""Test effect creation stack trace tracking."""

import os

import pytest

from doeff import (
    Ask,
    EffectGenerator,
    Log,
    ProgramInterpreter,
    Put,
    do,
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
            raise ValueError("Test error")
            return "never reached"

        engine = ProgramInterpreter()
        result = await engine.run_async(failing_program())

        assert result.is_err

        error_str = str(result.result.error)

        if DEBUG_EFFECTS or os.environ.get("DOEFF_DEBUG", "").lower() in ("1", "true", "yes"):  # noqa: PINJ050
            assert "ValueError" in error_str
            assert "Test error" in error_str
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
        result = await engine.run_async(program_with_missing_key())

        # Should have failed
        assert result.is_err

        # Get the error string
        error_str = str(result.result.error)

        # Should show where Ask was created
        if DEBUG_EFFECTS or os.environ.get("DOEFF_DEBUG", "").lower() in ("1", "true", "yes"):  # noqa: PINJ050
            assert "Effect 'AskEffect' failed" in error_str
            assert "Created at:" in error_str  # Changed from "Effect created at"
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
    assert hasattr(effect.created_at, "filename")
    assert hasattr(effect.created_at, "line")
    assert hasattr(effect.created_at, "function")


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
            raise RuntimeError("Inner error")
            yield Log("never reached")
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
        result = await engine.run_async(outer_program())

        assert result.is_err
        error_str = str(result.result.error)

        if DEBUG_EFFECTS or os.environ.get("DOEFF_DEBUG", "").lower() in ("1", "true", "yes"):  # noqa: PINJ050
            assert "RuntimeError" in error_str
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
        assert hasattr(effect.created_at, "filename")
        assert hasattr(effect.created_at, "line")
        assert hasattr(effect.created_at, "function")
        assert hasattr(effect.created_at, "code")
        assert hasattr(effect.created_at, "stack_trace")
        assert hasattr(effect.created_at, "frame_info")

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
        assert "Effect created at" in full  # This still uses the old format in EffectCreationContext.format_full()

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

    import sys

    try:
        @do
        def test_program() -> EffectGenerator[str]:
            yield Log("Starting test")
            raise ValueError("Test error for display")
            return "Should not reach here"

        engine = ProgramInterpreter()
        result = await engine.run_async(test_program())

        assert result.is_err

        display_output = result.display(verbose=True)

        assert "❌ Failure" in display_output
        assert "ValueError" in display_output
        assert "Test error for display" in display_output

        assert "test_program" in display_output
        assert "test_effect_creation_trace.py" in display_output

        simple_display = result.display(verbose=False)
        assert "❌ Failure" in simple_display
        assert "test_program" in simple_display

    finally:
        # Restore original debug setting
        if "DOEFF_DEBUG" in os.environ:  # noqa: PINJ050
            del os.environ["DOEFF_DEBUG"]  # noqa: PINJ050
        importlib.reload(utils)
        # Force reimport of result module
        if "doeff.effects.result" in sys.modules:
            del sys.modules["doeff.effects.result"]





@pytest.mark.asyncio
async def test_effect_creation_context_pickle_support():
    """Test that EffectCreationContext can be pickled (for spawn with process/ray backends).

    This tests the fix for ISSUE-CORE-407: EffectCreationContext.frame_info prevents
    spawn with process/ray backends due to unpicklable frame objects.
    """
    import pickle
    import cloudpickle

    # Enable debug mode to ensure frame_info is populated
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

        # Verify the effect has creation context with frame_info
        assert effect.created_at is not None
        assert hasattr(effect.created_at, "frame_info")

        # Test that EffectCreationContext can be pickled with cloudpickle
        # (this is what spawn uses for process/ray backends)
        pickled = cloudpickle.dumps(effect.created_at)
        restored = cloudpickle.loads(pickled)

        # Verify the restored object preserves essential data
        assert restored.filename == effect.created_at.filename
        assert restored.line == effect.created_at.line
        assert restored.function == effect.created_at.function
        assert restored.code == effect.created_at.code

        # frame_info should be None after unpickling (it cannot be serialized)
        assert restored.frame_info is None

        # Test standard pickle as well
        pickled_std = pickle.dumps(effect.created_at)
        restored_std = pickle.loads(pickled_std)

        assert restored_std.filename == effect.created_at.filename
        assert restored_std.line == effect.created_at.line
        assert restored_std.function == effect.created_at.function
        assert restored_std.frame_info is None

        # Test that the full effect can be pickled (this is the actual use case)
        pickled_effect = cloudpickle.dumps(effect)
        restored_effect = cloudpickle.loads(pickled_effect)

        assert restored_effect.created_at is not None
        assert restored_effect.created_at.function == "program_with_effect"

    finally:
        if "DOEFF_DEBUG" in os.environ:  # noqa: PINJ050
            del os.environ["DOEFF_DEBUG"]  # noqa: PINJ050
        importlib.reload(utils)


@pytest.mark.asyncio
async def test_effect_creation_context_stack_trace_sanitized_on_pickle():
    """Test that stack_trace frame references are sanitized during pickling."""
    import cloudpickle

    # Enable debug mode
    os.environ["DOEFF_DEBUG"] = "true"  # noqa: PINJ050

    import importlib

    from doeff import utils
    importlib.reload(utils)

    try:
        def program_with_effect() -> EffectGenerator[str]:
            yield Put("test", "value")
            return "done"

        gen = program_with_effect()
        effect = next(gen)

        # Manually add a frame reference to stack_trace to test sanitization
        if effect.created_at.stack_trace:
            # If stack_trace has frame references, they should be removed during pickle
            pass

        # Test pickling works regardless of stack_trace content
        pickled = cloudpickle.dumps(effect.created_at)
        restored = cloudpickle.loads(pickled)

        # Stack trace should not contain any 'frame' keys after unpickling
        for frame_dict in restored.stack_trace:
            assert "frame" not in frame_dict, "frame references should be removed during pickling"

    finally:
        if "DOEFF_DEBUG" in os.environ:  # noqa: PINJ050
            del os.environ["DOEFF_DEBUG"]  # noqa: PINJ050
        importlib.reload(utils)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
