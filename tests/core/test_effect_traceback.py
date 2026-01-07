"""
Tests for EffectTraceback protocol and implementations.

ISSUE-CORE-429: Tests for the abstract traceback protocol and its implementations:
- EffectTraceback protocol
- PythonTraceback class
- CapturedTraceback implementing EffectTraceback
- Err with captured_traceback field
"""

import json
import time
import pytest
from typing import Any

from doeff._vendor import Err, Ok, Some, NOTHING, Maybe
from doeff.traceback import EffectTraceback, PythonTraceback, capture_python_traceback

try:
    from dataclasses import FrozenInstanceError
except ImportError:
    FrozenInstanceError = AttributeError  # type: ignore[misc, assignment]


class TestEffectTracebackProtocol:
    """Test EffectTraceback protocol compliance."""

    def test_python_traceback_is_effect_traceback(self):
        """PythonTraceback should be an instance of EffectTraceback protocol."""
        ex = ValueError("test")
        tb = PythonTraceback(exception=ex)
        assert isinstance(tb, EffectTraceback)

    def test_captured_traceback_is_effect_traceback(self):
        """CapturedTraceback should be an instance of EffectTraceback protocol."""
        from doeff.cesk_traceback import CapturedTraceback, CodeLocation, EffectFrame
        
        tb = CapturedTraceback(
            effect_frames=(
                EffectFrame(
                    location=CodeLocation(filename="test.py", lineno=10, function="test_func"),
                    frame_kind="kleisli_yield",
                ),
            ),
            python_frames=(),
            exception_type="ValueError",
            exception_message="test",
            exception_args=("test",),
            exception=ValueError("test"),
        )
        assert isinstance(tb, EffectTraceback)

    def test_protocol_requires_format(self):
        """EffectTraceback protocol requires format() method."""
        ex = ValueError("test")
        tb = PythonTraceback(exception=ex)
        result = tb.format()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_protocol_requires_format_short(self):
        """EffectTraceback protocol requires format_short() method."""
        ex = ValueError("test")
        tb = PythonTraceback(exception=ex)
        result = tb.format_short()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_protocol_requires_to_dict(self):
        """EffectTraceback protocol requires to_dict() method."""
        ex = ValueError("test")
        tb = PythonTraceback(exception=ex)
        result = tb.to_dict()
        assert isinstance(result, dict)


class TestPythonTraceback:
    """Test PythonTraceback class."""

    def test_creation_with_exception(self):
        """PythonTraceback can be created with just an exception."""
        ex = ValueError("test error")
        tb = PythonTraceback(exception=ex)
        assert tb.exception is ex
        assert tb.capture_timestamp is not None

    def test_creation_with_traceback_obj(self):
        """PythonTraceback captures __traceback__ automatically."""
        try:
            raise ValueError("test error")
        except ValueError as ex:
            tb = PythonTraceback(exception=ex)
            assert tb.traceback_obj is not None

    def test_capture_timestamp_auto_set(self):
        """PythonTraceback sets capture_timestamp automatically."""
        before = time.time()
        ex = ValueError("test")
        tb = PythonTraceback(exception=ex)
        after = time.time()
        assert before <= tb.capture_timestamp <= after

    def test_format_contains_exception_info(self):
        """format() should contain exception type and message."""
        ex = ValueError("test error message")
        tb = PythonTraceback(exception=ex)
        formatted = tb.format()
        assert "ValueError" in formatted
        assert "test error message" in formatted

    def test_format_contains_traceback_header(self):
        """format() should contain Python Traceback header."""
        ex = ValueError("test")
        tb = PythonTraceback(exception=ex)
        formatted = tb.format()
        assert "Python Traceback" in formatted

    def test_format_with_real_traceback(self):
        """format() should show file/line info for real tracebacks."""
        try:
            raise ValueError("real error")
        except ValueError as ex:
            tb = PythonTraceback(exception=ex)
            formatted = tb.format()
            assert "test_effect_traceback.py" in formatted

    def test_format_short_contains_exception(self):
        """format_short() should contain exception type and message."""
        ex = ValueError("short test")
        tb = PythonTraceback(exception=ex)
        short = tb.format_short()
        assert "ValueError" in short
        assert "short test" in short

    def test_format_short_is_single_line(self):
        """format_short() should be a single line."""
        try:
            raise ValueError("multiline\nerror")
        except ValueError as ex:
            tb = PythonTraceback(exception=ex)
            short = tb.format_short()
            assert short.count("\n") == 0

    def test_to_dict_schema(self):
        """to_dict() should follow expected schema."""
        ex = ValueError("dict test")
        tb = PythonTraceback(exception=ex)
        d = tb.to_dict()
        
        assert d["version"] == "1.0"
        assert d["type"] == "python"
        assert "frames" in d
        assert "exception" in d
        assert "metadata" in d
        
        assert d["exception"]["type"] == "ValueError"
        assert d["exception"]["message"] == "dict test"
        assert d["metadata"]["interpreter"] == "program_interpreter"

    def test_to_dict_is_json_serializable(self):
        """to_dict() should return JSON-serializable dict."""
        try:
            raise ValueError("json test")
        except ValueError as ex:
            tb = PythonTraceback(exception=ex)
            d = tb.to_dict()
            json_str = json.dumps(d)
            parsed = json.loads(json_str)
            assert parsed["exception"]["type"] == "ValueError"

    def test_to_dict_frames_with_traceback(self):
        """to_dict() should include frame info when traceback exists."""
        try:
            raise ValueError("frames test")
        except ValueError as ex:
            tb = PythonTraceback(exception=ex)
            d = tb.to_dict()
            assert len(d["frames"]) > 0
            frame = d["frames"][-1]
            assert "filename" in frame
            assert "lineno" in frame
            assert "function" in frame

    def test_frozen_dataclass(self):
        """PythonTraceback should be frozen (immutable)."""
        ex = ValueError("test")
        tb = PythonTraceback(exception=ex)
        with pytest.raises(AttributeError):
            tb.exception = RuntimeError("new")


class TestCapturePythonTraceback:
    """Test capture_python_traceback convenience function."""

    def test_captures_from_exception(self):
        """capture_python_traceback creates PythonTraceback from exception."""
        try:
            raise ValueError("capture test")
        except ValueError as ex:
            tb = capture_python_traceback(ex)
            assert isinstance(tb, PythonTraceback)
            assert tb.exception is ex
            assert tb.traceback_obj is not None

    def test_sets_timestamp(self):
        """capture_python_traceback sets timestamp."""
        before = time.time()
        ex = ValueError("test")
        tb = capture_python_traceback(ex)
        after = time.time()
        assert before <= tb.capture_timestamp <= after


class TestCapturedTracebackEffectTracebackMethods:
    """Test CapturedTraceback implements EffectTraceback protocol methods."""

    def _create_captured_traceback(self, exception: BaseException) -> Any:
        """Helper to create a CapturedTraceback."""
        from doeff.cesk_traceback import CapturedTraceback, CodeLocation, EffectFrame, PythonFrame
        
        return CapturedTraceback(
            effect_frames=(
                EffectFrame(
                    location=CodeLocation(filename="outer.py", lineno=10, function="outer_func"),
                    frame_kind="kleisli_yield",
                ),
                EffectFrame(
                    location=CodeLocation(filename="inner.py", lineno=20, function="inner_func"),
                    frame_kind="kleisli_yield",
                ),
            ),
            python_frames=(
                PythonFrame(
                    location=CodeLocation(filename="pure.py", lineno=30, function="pure_func", code="raise ex"),
                ),
            ),
            exception_type=type(exception).__name__,
            exception_message=str(exception),
            exception_args=exception.args,
            exception=exception,
        )

    def test_format_method_exists(self):
        """CapturedTraceback has format() method."""
        tb = self._create_captured_traceback(ValueError("test"))
        assert hasattr(tb, "format")
        result = tb.format()
        assert isinstance(result, str)

    def test_format_contains_effect_frames(self):
        """format() includes effect frame info."""
        tb = self._create_captured_traceback(ValueError("test"))
        formatted = tb.format()
        assert "outer_func" in formatted
        assert "inner_func" in formatted

    def test_format_contains_python_frames(self):
        """format() includes python frame info."""
        tb = self._create_captured_traceback(ValueError("test"))
        formatted = tb.format()
        assert "pure_func" in formatted

    def test_format_contains_exception(self):
        """format() includes exception info."""
        tb = self._create_captured_traceback(ValueError("format test"))
        formatted = tb.format()
        assert "ValueError" in formatted
        assert "format test" in formatted

    def test_format_short_method_exists(self):
        """CapturedTraceback has format_short() method."""
        tb = self._create_captured_traceback(ValueError("test"))
        assert hasattr(tb, "format_short")
        result = tb.format_short()
        assert isinstance(result, str)

    def test_format_short_contains_chain(self):
        """format_short() shows function chain."""
        tb = self._create_captured_traceback(KeyError("short test"))
        short = tb.format_short()
        assert "KeyError" in short
        assert "short test" in short

    def test_to_dict_method_exists(self):
        """CapturedTraceback has to_dict() method."""
        tb = self._create_captured_traceback(ValueError("test"))
        assert hasattr(tb, "to_dict")
        result = tb.to_dict()
        assert isinstance(result, dict)

    def test_to_dict_is_json_serializable(self):
        """to_dict() returns JSON-serializable dict."""
        tb = self._create_captured_traceback(ValueError("json test"))
        d = tb.to_dict()
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert parsed["exception"]["type"] == "ValueError"

    def test_to_dict_schema(self):
        """to_dict() follows expected schema."""
        tb = self._create_captured_traceback(ValueError("schema test"))
        d = tb.to_dict()
        
        assert d["version"] == "1.0"
        assert "effect_frames" in d
        assert "python_frames" in d
        assert "exception" in d
        assert "metadata" in d
        assert d["metadata"]["interpreter_version"] == "cesk-v1"


class TestErrWithCapturedTraceback:
    """Test Err class with captured_traceback field."""

    def test_err_has_captured_traceback_field(self):
        """Err should have captured_traceback field."""
        err = Err(ValueError("test"))
        assert hasattr(err, "captured_traceback")

    def test_err_captured_traceback_default_is_nothing(self):
        """Err.captured_traceback defaults to NOTHING."""
        err = Err(ValueError("test"))
        assert err.captured_traceback.is_none()

    def test_err_with_python_traceback(self):
        """Err can store PythonTraceback in captured_traceback."""
        ex = ValueError("test")
        tb = PythonTraceback(exception=ex)
        err = Err(ex, captured_traceback=Some(tb))
        
        assert err.captured_traceback.is_some()
        assert err.captured_traceback.unwrap() is tb

    def test_err_with_captured_traceback(self):
        """Err can store CapturedTraceback in captured_traceback."""
        from doeff.cesk_traceback import CapturedTraceback, CodeLocation, EffectFrame
        
        ex = ValueError("test")
        tb = CapturedTraceback(
            effect_frames=(
                EffectFrame(
                    location=CodeLocation(filename="test.py", lineno=10, function="test"),
                    frame_kind="kleisli_yield",
                ),
            ),
            python_frames=(),
            exception_type="ValueError",
            exception_message="test",
            exception_args=("test",),
            exception=ex,
        )
        err = Err(ex, captured_traceback=Some(tb))
        
        assert err.captured_traceback.is_some()
        assert err.captured_traceback.unwrap() is tb

    def test_err_captured_traceback_is_maybe_type(self):
        """Err.captured_traceback should be Maybe type."""
        err = Err(ValueError("test"))
        assert isinstance(err.captured_traceback, Maybe)

    def test_err_frozen(self):
        """Err should be frozen (immutable) - cannot assign via normal attribute access."""
        err = Err(ValueError("test"))
        with pytest.raises((AttributeError, TypeError, FrozenInstanceError)):
            err.error = RuntimeError("new")

    def test_err_pattern_matching_with_traceback(self):
        """Err can be pattern matched with captured_traceback."""
        ex = ValueError("pattern test")
        tb = PythonTraceback(exception=ex)
        err = Err(ex, captured_traceback=Some(tb))
        
        match err:
            case Err(error=e, captured_traceback=Some(trace)):
                assert isinstance(e, ValueError)
                assert isinstance(trace, PythonTraceback)
            case _:
                pytest.fail("Pattern match failed")

    def test_err_pattern_matching_without_traceback(self):
        """Err without traceback can be pattern matched."""
        err = Err(ValueError("no trace"))
        
        match err:
            case Err(error=e, captured_traceback=tb) if tb.is_none():
                assert isinstance(e, ValueError)
            case _:
                pytest.fail("Pattern match failed")


class TestMaybeWithEffectTraceback:
    """Test Maybe[EffectTraceback] usage patterns."""

    def test_some_with_python_traceback(self):
        """Some can wrap PythonTraceback."""
        tb = PythonTraceback(exception=ValueError("test"))
        maybe_tb = Some(tb)
        assert maybe_tb.is_some()
        assert maybe_tb.unwrap() is tb

    def test_nothing_for_no_traceback(self):
        """NOTHING represents absence of traceback."""
        maybe_tb = NOTHING
        assert maybe_tb.is_none()
        with pytest.raises(RuntimeError):
            maybe_tb.unwrap()

    def test_maybe_map_on_traceback(self):
        """Maybe.map works with EffectTraceback."""
        tb = PythonTraceback(exception=ValueError("map test"))
        maybe_tb: Maybe[EffectTraceback] = Some(tb)
        
        result = maybe_tb.map(lambda t: t.format_short())
        assert result.is_some()
        assert "ValueError" in result.unwrap()

    def test_maybe_map_on_nothing(self):
        """Maybe.map on NOTHING returns NOTHING."""
        maybe_tb: Maybe[EffectTraceback] = NOTHING
        result = maybe_tb.map(lambda t: t.format_short())
        assert result.is_none()


class TestTracebackInteroperability:
    """Test that both traceback types work interchangeably."""

    def test_both_satisfy_protocol(self):
        """Both PythonTraceback and CapturedTraceback satisfy EffectTraceback."""
        from doeff.cesk_traceback import CapturedTraceback, CodeLocation, EffectFrame
        
        py_tb = PythonTraceback(exception=ValueError("py"))
        cesk_tb = CapturedTraceback(
            effect_frames=(
                EffectFrame(
                    location=CodeLocation(filename="t.py", lineno=1, function="f"),
                    frame_kind="kleisli_yield",
                ),
            ),
            python_frames=(),
            exception_type="ValueError",
            exception_message="cesk",
            exception_args=("cesk",),
            exception=ValueError("cesk"),
        )
        
        assert isinstance(py_tb, EffectTraceback)
        assert isinstance(cesk_tb, EffectTraceback)

    def test_generic_function_accepts_both(self):
        """A function typed for EffectTraceback accepts both implementations."""
        from doeff.cesk_traceback import CapturedTraceback, CodeLocation, EffectFrame
        
        def format_any_traceback(tb: EffectTraceback) -> str:
            return tb.format_short()
        
        py_tb = PythonTraceback(exception=ValueError("py"))
        cesk_tb = CapturedTraceback(
            effect_frames=(
                EffectFrame(
                    location=CodeLocation(filename="t.py", lineno=1, function="f"),
                    frame_kind="kleisli_yield",
                ),
            ),
            python_frames=(),
            exception_type="ValueError",
            exception_message="cesk",
            exception_args=("cesk",),
            exception=ValueError("cesk"),
        )
        
        py_result = format_any_traceback(py_tb)
        cesk_result = format_any_traceback(cesk_tb)
        
        assert "ValueError" in py_result
        assert "ValueError" in cesk_result

    def test_err_accepts_both_traceback_types(self):
        """Err.captured_traceback accepts both PythonTraceback and CapturedTraceback."""
        from doeff.cesk_traceback import CapturedTraceback, CodeLocation, EffectFrame
        
        py_tb = PythonTraceback(exception=ValueError("py"))
        cesk_tb = CapturedTraceback(
            effect_frames=(
                EffectFrame(
                    location=CodeLocation(filename="t.py", lineno=1, function="f"),
                    frame_kind="kleisli_yield",
                ),
            ),
            python_frames=(),
            exception_type="ValueError",
            exception_message="cesk",
            exception_args=("cesk",),
            exception=ValueError("cesk"),
        )
        
        err_py = Err(ValueError("py"), captured_traceback=Some(py_tb))
        err_cesk = Err(ValueError("cesk"), captured_traceback=Some(cesk_tb))
        
        assert err_py.captured_traceback.unwrap().format_short()
        assert err_cesk.captured_traceback.unwrap().format_short()
