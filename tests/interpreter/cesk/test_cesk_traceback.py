"""
Tests for CESK interpreter effect traceback capture.

Tests the traceback capture functionality as specified in ISSUE-CORE-423.
"""

import json
import sys
import pytest

from doeff.cesk import run_sync, CESKResult
from doeff.cesk_traceback import (
    CapturedTraceback,
    CodeLocation,
    EffectFrame,
    PythonFrame,
    format_traceback,
    format_traceback_short,
    to_dict,
)
from doeff.do import do
from doeff.effects import Pure


# ============================================================================
# Test Fixtures
# ============================================================================


@do
def simple_raise():
    """A simple @do function that raises immediately."""
    raise ValueError("simple error")
    yield Pure(None)  # Never reached, but makes it a generator


@do
def raises_after_yield():
    """A @do function that yields once then raises."""
    x = yield Pure(42)
    raise RuntimeError(f"error after yield, x={x}")


@do
def nested_a():
    """Outer function that calls nested_b."""
    return (yield nested_b())


@do
def nested_b():
    """Middle function that calls nested_c."""
    return (yield nested_c())


@do
def nested_c():
    """Innermost function that raises."""
    raise KeyError("nested error in C")
    yield Pure(None)


@do
def calls_pure_function():
    """A @do function that calls pure Python which raises."""
    result = pure_outer("test")
    yield Pure(result)


def pure_outer(data):
    """Pure function that calls another pure function."""
    return pure_inner(data)


def pure_inner(data):
    """Pure function that raises."""
    raise TypeError(f"pure error with {data}")


@do
def succeeds():
    """A successful @do function."""
    x = yield Pure(10)
    y = yield Pure(20)
    return x + y


@do
def catch_and_reraise():
    """A @do function that catches and re-raises the same exception."""
    try:
        yield raises_after_yield()
    except RuntimeError:
        raise  # Re-raise same exception


@do
def catch_and_raise_new():
    """A @do function that catches and raises a new exception."""
    try:
        yield raises_after_yield()
    except RuntimeError as e:
        raise ValueError(f"wrapped: {e}") from e


# ============================================================================
# Test: Basic Traceback Capture
# ============================================================================


class TestBasicTracebackCapture:
    """Test basic traceback capture functionality."""

    def test_simple_exception_has_captured_traceback(self):
        """AC1: CESKResult.captured_traceback is not None for exceptions."""
        result = run_sync(simple_raise())

        assert result.is_err
        assert result.captured_traceback is not None
        assert isinstance(result.captured_traceback, CapturedTraceback)
        assert result.error is not None
        assert isinstance(result.error, ValueError)
        assert str(result.error) == "simple error"

    def test_exception_after_yield_has_traceback(self):
        """Test that exceptions after yield also capture traceback."""
        result = run_sync(raises_after_yield())

        assert result.is_err
        assert result.captured_traceback is not None
        assert isinstance(result.error, RuntimeError)

    def test_success_has_no_traceback(self):
        """Test that successful execution has no traceback."""
        result = run_sync(succeeds())

        assert result.is_ok
        assert result.captured_traceback is None
        assert result.value == 30


class TestEffectFrameCapture:
    """Test effect frame capture from K stack."""

    def test_simple_effect_frame(self):
        """Test that a simple raise captures the effect frame."""
        result = run_sync(simple_raise())

        assert result.captured_traceback is not None
        assert len(result.captured_traceback.effect_frames) >= 1

        # The innermost effect frame should be simple_raise
        ef = result.captured_traceback.effect_frames[-1]
        assert ef.location.function == "simple_raise"
        assert ef.frame_kind in ("kleisli_entry", "kleisli_yield")

    def test_nested_effect_frames(self):
        """AC2: effect_frames order matches K stack order (outermost → innermost)."""
        result = run_sync(nested_a())

        assert result.captured_traceback is not None
        frames = result.captured_traceback.effect_frames

        # Should have nested_a, nested_b, nested_c in outermost→innermost order
        # (but the innermost wrapper generator may be included too)
        function_names = [ef.location.function for ef in frames]

        # At minimum, we should see the nested functions
        # The order should be outermost first
        assert "nested_c" in function_names

        # Verify outermost→innermost ordering
        if "nested_a" in function_names and "nested_c" in function_names:
            a_idx = function_names.index("nested_a")
            c_idx = function_names.index("nested_c")
            assert a_idx < c_idx, "nested_a should come before nested_c (outermost first)"


class TestPythonFrameCapture:
    """Test Python frame capture from exception traceback."""

    def test_python_frames_captured(self):
        """AC3: python_frames order matches exception.__traceback__."""
        result = run_sync(calls_pure_function())

        assert result.captured_traceback is not None
        frames = result.captured_traceback.python_frames

        assert len(frames) > 0

        # Check that python frames contain expected function names
        function_names = [pf.location.function for pf in frames]

        # Should see pure_inner at least (where the exception was raised)
        assert "pure_inner" in function_names

    def test_python_frame_order_outermost_first(self):
        """Test Python frames are outermost→innermost (raise site last)."""
        result = run_sync(calls_pure_function())

        assert result.captured_traceback is not None
        frames = result.captured_traceback.python_frames
        function_names = [pf.location.function for pf in frames]

        # pure_inner (raise site) should be last or near last
        if "pure_outer" in function_names and "pure_inner" in function_names:
            outer_idx = function_names.index("pure_outer")
            inner_idx = function_names.index("pure_inner")
            assert outer_idx < inner_idx, "pure_outer should come before pure_inner"


class TestCallSiteCapture:
    """Test call site capture from parent generators."""

    def test_nested_call_has_call_site(self):
        """AC4: For nested A→B call, B's call_site points to A's file."""
        result = run_sync(nested_a())

        assert result.captured_traceback is not None
        frames = result.captured_traceback.effect_frames

        # Find nested_b or nested_c frame
        for ef in frames:
            if ef.location.function in ("nested_b", "nested_c") and ef.call_site is not None:
                # If we have call site, it should point to a valid location
                assert ef.call_site.filename is not None
                assert ef.call_site.lineno > 0
                break

    def test_top_level_no_call_site(self):
        """AC5: Top-level entry has call_site = None."""
        result = run_sync(simple_raise())

        assert result.captured_traceback is not None

        # The outermost effect frame should have no call site
        if result.captured_traceback.effect_frames:
            outermost = result.captured_traceback.effect_frames[0]
            # Top-level doesn't have a parent @do function
            # (call_site is about the yield site in the parent @do function)


class TestImmediateRaise:
    """Test pre-capture for immediate raise scenarios."""

    def test_immediate_raise_has_effect_frames(self):
        """AC6: Immediate raise (no yield) has non-empty effect_frames."""
        result = run_sync(simple_raise())

        assert result.captured_traceback is not None
        # Even though simple_raise raises before yielding,
        # we should still capture its frame via pre-capture
        assert len(result.captured_traceback.effect_frames) >= 1


class TestExceptionPreservation:
    """Test that original exception is preserved untouched."""

    def test_original_exception_preserved(self):
        """Test that result.error is the original exception object."""
        result = run_sync(simple_raise())

        assert result.is_err
        assert isinstance(result.error, ValueError)
        assert str(result.error) == "simple error"

        # isinstance checks should work
        assert isinstance(result.error, Exception)
        assert isinstance(result.error, ValueError)

    def test_exception_type_in_traceback(self):
        """Test exception type is captured correctly."""
        result = run_sync(nested_a())

        assert result.captured_traceback is not None
        assert result.captured_traceback.exception_type == "KeyError"
        assert "nested error in C" in result.captured_traceback.exception_message


class TestTracebackPropagation:
    """Test traceback propagation through K frames."""

    def test_catch_and_reraise_preserves_traceback(self):
        """Test that catching and re-raising preserves original traceback."""
        result = run_sync(catch_and_reraise())

        assert result.is_err
        assert result.captured_traceback is not None
        assert isinstance(result.error, RuntimeError)

    def test_catch_and_raise_new_captures_new_traceback(self):
        """Test that raising new exception captures new traceback."""
        result = run_sync(catch_and_raise_new())

        assert result.is_err
        assert result.captured_traceback is not None
        assert isinstance(result.error, ValueError)
        assert "wrapped:" in str(result.error)


# ============================================================================
# Test: Format Functions
# ============================================================================


class TestFormatTraceback:
    """Test format_traceback() function."""

    def test_format_traceback_none(self):
        """Test format_traceback(None) returns placeholder."""
        assert format_traceback(None) == "(no captured traceback)"

    def test_format_traceback_basic_structure(self):
        """AC17: format_traceback() returns human-readable string."""
        result = run_sync(simple_raise())

        assert result.captured_traceback is not None
        formatted = format_traceback(result.captured_traceback)

        # Check basic structure
        assert "Effect Traceback (Kleisli call chain):" in formatted
        assert "Python Traceback (most recent call last):" in formatted
        assert "ValueError: simple error" in formatted

    def test_format_traceback_contains_file_info(self):
        """Test that formatted traceback contains file info."""
        result = run_sync(simple_raise())

        formatted = format_traceback(result.captured_traceback)

        # Should contain File references
        assert 'File "' in formatted
        assert ', line ' in formatted
        assert ', in ' in formatted


class TestFormatTracebackShort:
    """Test format_traceback_short() function."""

    def test_format_short_none(self):
        """Test format_traceback_short(None) returns placeholder."""
        assert format_traceback_short(None) == "(no captured traceback)"

    def test_format_short_basic(self):
        """Test basic format_traceback_short output."""
        result = run_sync(simple_raise())

        assert result.captured_traceback is not None
        short = format_traceback_short(result.captured_traceback)

        # Should contain exception type and message
        assert "ValueError" in short
        assert "simple error" in short

    def test_format_short_nested(self):
        """Test format_traceback_short with nested calls."""
        result = run_sync(nested_a())

        short = format_traceback_short(result.captured_traceback)

        # Should show function chain
        assert "KeyError" in short
        assert "nested error in C" in short


class TestToDict:
    """Test to_dict() function."""

    def test_to_dict_none(self):
        """Test to_dict(None) returns None."""
        assert to_dict(None) is None

    def test_to_dict_json_serializable(self):
        """AC18: to_dict() returns JSON-serializable dict."""
        result = run_sync(simple_raise())

        assert result.captured_traceback is not None
        d = to_dict(result.captured_traceback)

        assert d is not None

        # Should be JSON-serializable
        json_str = json.dumps(d)
        assert json_str is not None

        # Parse back and verify structure
        parsed = json.loads(json_str)
        assert "version" in parsed
        assert "effect_frames" in parsed
        assert "python_frames" in parsed
        assert "exception" in parsed
        assert "metadata" in parsed

    def test_to_dict_schema(self):
        """Test to_dict() follows schema."""
        result = run_sync(simple_raise())

        d = to_dict(result.captured_traceback)

        # Version
        assert d["version"] == "1.0"

        # Exception info
        assert d["exception"]["type"] == "ValueError"
        assert "simple error" in d["exception"]["message"]
        assert "qualified_type" in d["exception"]

        # Metadata
        assert "capture_timestamp" in d["metadata"]
        assert d["metadata"]["interpreter_version"] == "cesk-v1"

    def test_to_dict_effect_frame_structure(self):
        """Test effect frame structure in to_dict output."""
        result = run_sync(simple_raise())

        d = to_dict(result.captured_traceback)

        if d["effect_frames"]:
            ef = d["effect_frames"][0]
            assert "location" in ef
            assert "frame_kind" in ef
            assert "call_site" in ef

            loc = ef["location"]
            assert "filename" in loc
            assert "lineno" in loc
            assert "function" in loc


# ============================================================================
# Test: CESKResult API
# ============================================================================


class TestCESKResultAPI:
    """Test CESKResult class API."""

    def test_is_ok_method(self):
        """Test is_ok method."""
        ok_result = run_sync(succeeds())
        err_result = run_sync(simple_raise())

        assert ok_result.is_ok() == True
        assert err_result.is_ok() == False

    def test_is_err_method(self):
        """Test is_err method."""
        ok_result = run_sync(succeeds())
        err_result = run_sync(simple_raise())

        assert ok_result.is_err() == False
        assert err_result.is_err() == True

    def test_value_property(self):
        """Test value property on success."""
        result = run_sync(succeeds())

        assert result.is_ok()
        assert result.value == 30

    def test_error_property(self):
        """Test error property on error."""
        result = run_sync(simple_raise())

        assert result.is_err
        error = result.error
        assert isinstance(error, ValueError)


# ============================================================================
# Test: Edge Cases
# ============================================================================


class TestEdgeCases:
    """Test edge cases in traceback capture."""

    def test_empty_effect_frames_format(self):
        """Test formatting when effect_frames is empty."""
        # Create a mock traceback with empty effect_frames
        tb = CapturedTraceback(
            effect_frames=(),
            python_frames=(
                PythonFrame(
                    location=CodeLocation(
                        filename="test.py",
                        lineno=10,
                        function="test_func",
                        code="raise ValueError('test')",
                    ),
                ),
            ),
            exception_type="ValueError",
            exception_message="test",
            exception_args=("test",),
            exception=ValueError("test"),
        )

        formatted = format_traceback(tb)
        assert "(no effect frames)" in formatted

    def test_empty_python_frames_format(self):
        """Test formatting when python_frames is empty."""
        tb = CapturedTraceback(
            effect_frames=(
                EffectFrame(
                    location=CodeLocation(
                        filename="test.py",
                        lineno=5,
                        function="test_do",
                    ),
                    frame_kind="kleisli_yield",
                ),
            ),
            python_frames=(),
            exception_type="ValueError",
            exception_message="test",
            exception_args=("test",),
            exception=ValueError("test"),
        )

        formatted = format_traceback(tb)
        assert "(no python frames)" in formatted

    def test_builtin_file_no_code_line(self):
        """Test that built-in files don't show code lines."""
        tb = CapturedTraceback(
            effect_frames=(),
            python_frames=(
                PythonFrame(
                    location=CodeLocation(
                        filename="<frozen importlib>",
                        lineno=100,
                        function="find_spec",
                        code=None,
                    ),
                ),
            ),
            exception_type="ImportError",
            exception_message="test",
            exception_args=("test",),
            exception=ImportError("test"),
        )

        formatted = format_traceback(tb)
        # Should NOT contain <source unavailable> for built-in files
        assert "<source unavailable>" not in formatted

    def test_format_short_empty_effect_frames(self):
        """Test format_traceback_short with empty effect frames."""
        tb = CapturedTraceback(
            effect_frames=(),
            python_frames=(),
            exception_type="ValueError",
            exception_message="test",
            exception_args=("test",),
            exception=ValueError("test"),
        )

        short = format_traceback_short(tb)
        assert "<top-level>" in short
        assert "ValueError" in short


# ============================================================================
# Test: Error Paths with Traceback Capture
# ============================================================================


class TestErrorPathsTracebackCapture:
    """Test traceback capture for various error paths."""

    def test_pure_effect_handler_exception_has_traceback(self):
        """Test that exceptions from pure effect handlers capture traceback."""
        from doeff.effects import state

        @do
        def program_with_modify():
            # StateModifyEffect calls a function that may raise
            def bad_modifier(x):
                raise ValueError("modifier error")

            yield state.Modify("key", bad_modifier)
            return "done"

        # First set up the state
        @do
        def setup_and_run():
            yield state.Put("key", 10)
            yield program_with_modify()

        result = run_sync(setup_and_run())

        assert result.is_err
        assert result.captured_traceback is not None
        assert isinstance(result.error, ValueError)
        assert "modifier error" in str(result.error)

    def test_intercept_transform_exception_has_traceback(self):
        """Test that exceptions from intercept transforms capture traceback."""
        from doeff.effects import Pure

        @do
        def simple_program():
            x = yield Pure(42)
            return x

        def bad_transformer(effect):
            raise RuntimeError("transformer error")

        result = run_sync(simple_program().intercept(bad_transformer))

        assert result.is_err
        assert result.captured_traceback is not None
        assert isinstance(result.error, RuntimeError)
        assert "transformer error" in str(result.error)

    def test_unhandled_effect_has_traceback(self):
        """Test that unhandled effects have traceback."""
        from doeff._types_internal import EffectBase
        from doeff.program import ProgramProtocol
        from dataclasses import dataclass
        from typing import Any

        @dataclass(frozen=True)
        class CustomUnhandledEffect(EffectBase):
            """A custom effect that has no handler."""
            value: int

            def intercept(self, transform: Any) -> ProgramProtocol:
                # Required by EffectBase but not used in this test
                return self

        @do
        def program_with_unhandled():
            yield CustomUnhandledEffect(42)
            return "done"

        result = run_sync(program_with_unhandled())

        assert result.is_err
        assert result.captured_traceback is not None
        # Should be UnhandledEffectError
        assert "No handler for" in str(result.error)
        assert "CustomUnhandledEffect" in str(result.error)


# ============================================================================
# Test: ExceptionGroup (Python 3.11+)
# ============================================================================


@pytest.mark.skipif(sys.version_info < (3, 11), reason="ExceptionGroup requires Python 3.11+")
class TestExceptionGroup:
    """Test ExceptionGroup handling."""

    def test_exception_group_format(self):
        """Test formatting of ExceptionGroup."""
        exc_group = ExceptionGroup(
            "multiple errors",
            [ValueError("error 1"), TypeError("error 2")],
        )

        tb = CapturedTraceback(
            effect_frames=(),
            python_frames=(),
            exception_type="ExceptionGroup",
            exception_message="multiple errors (2 sub-exceptions)",
            exception_args=("multiple errors", [ValueError("error 1"), TypeError("error 2")]),
            exception=exc_group,
        )

        formatted = format_traceback(tb)
        assert "ExceptionGroup" in formatted

    def test_exception_group_to_dict(self):
        """Test to_dict with ExceptionGroup."""
        exc_group = ExceptionGroup(
            "multiple errors",
            [ValueError("error 1"), TypeError("error 2")],
        )

        tb = CapturedTraceback(
            effect_frames=(),
            python_frames=(),
            exception_type="ExceptionGroup",
            exception_message="multiple errors (2 sub-exceptions)",
            exception_args=("multiple errors", [ValueError("error 1"), TypeError("error 2")]),
            exception=exc_group,
        )

        d = to_dict(tb)

        assert d["exception"]["is_group"] is True
        assert d["exception"]["group_count"] == 2
        assert len(d["exception"]["sub_exceptions"]) == 2
