"""
Tests for Safe effect with traceback capture in CESK and ProgramInterpreter.

ISSUE-CORE-429: Safe effect should capture K stack trace before unwinding.
"""

import pytest

from doeff._vendor import Err, Ok, Some, NOTHING
from doeff.do import do
from doeff.effects import Pure, Safe, Fail
from doeff.program import Program


@do
def raises_value_error():
    """A @do function that raises ValueError."""
    raise ValueError("test error")
    yield Pure(None)


@do
def nested_outer():
    """Outer function in nested call chain."""
    return (yield nested_middle())


@do
def nested_middle():
    """Middle function in nested call chain."""
    return (yield nested_inner())


@do
def nested_inner():
    """Inner function that raises."""
    raise KeyError("nested error")
    yield Pure(None)


@do
def succeeds():
    """A successful @do function."""
    x = yield Pure(42)
    return x


@do
def fails_with_effect():
    """A @do function that fails via Fail effect."""
    yield Fail(RuntimeError("effect failure"))
    return "never reached"


class TestSafeCESK:
    """Test Safe effect with CESK interpreter."""

    def test_safe_wraps_success_in_ok(self):
        """Safe wraps successful value in Ok."""
        from doeff.cesk import run_sync

        @do
        def program():
            result = yield Safe(succeeds())
            return result

        result = run_sync(program())
        assert result.is_ok
        value = result.value
        assert isinstance(value, Ok)
        assert value.value == 42

    def test_safe_wraps_error_in_err(self):
        """Safe wraps error in Err."""
        from doeff.cesk import run_sync

        @do
        def program():
            result = yield Safe(raises_value_error())
            return result

        result = run_sync(program())
        assert result.is_ok
        value = result.value
        assert isinstance(value, Err)
        assert isinstance(value.error, ValueError)
        assert str(value.error) == "test error"

    def test_safe_captures_traceback_on_error(self):
        """Safe captures K stack traceback when error occurs."""
        from doeff.cesk import run_sync
        from doeff.cesk_traceback import CapturedTraceback

        @do
        def program():
            result = yield Safe(raises_value_error())
            return result

        result = run_sync(program())
        assert result.is_ok
        value = result.value
        assert isinstance(value, Err)
        assert value.captured_traceback.is_some()
        trace = value.captured_traceback.unwrap()
        assert isinstance(trace, CapturedTraceback)

    def test_safe_traceback_contains_effect_frames(self):
        """Safe captured traceback contains effect frames."""
        from doeff.cesk import run_sync
        from doeff.cesk_traceback import CapturedTraceback

        @do
        def program():
            result = yield Safe(nested_outer())
            return result

        result = run_sync(program())
        assert result.is_ok
        value = result.value
        assert isinstance(value, Err)
        trace = value.captured_traceback.unwrap()
        assert isinstance(trace, CapturedTraceback)
        assert len(trace.effect_frames) > 0
        func_names = [ef.location.function for ef in trace.effect_frames]
        assert "nested_inner" in func_names

    def test_safe_traceback_format_works(self):
        """Safe captured traceback can be formatted."""
        from doeff.cesk import run_sync

        @do
        def program():
            result = yield Safe(raises_value_error())
            return result

        result = run_sync(program())
        value = result.value
        trace = value.captured_traceback.unwrap()
        formatted = trace.format()
        assert "ValueError" in formatted
        assert "test error" in formatted
        assert "raises_value_error" in formatted

    def test_safe_traceback_format_short_works(self):
        """Safe captured traceback short format works."""
        from doeff.cesk import run_sync

        @do
        def program():
            result = yield Safe(raises_value_error())
            return result

        result = run_sync(program())
        value = result.value
        trace = value.captured_traceback.unwrap()
        short = trace.format_short()
        assert "ValueError" in short
        assert "test error" in short

    def test_safe_traceback_to_dict_works(self):
        """Safe captured traceback can be serialized."""
        from doeff.cesk import run_sync
        import json

        @do
        def program():
            result = yield Safe(raises_value_error())
            return result

        result = run_sync(program())
        value = result.value
        trace = value.captured_traceback.unwrap()
        d = trace.to_dict()
        json_str = json.dumps(d)
        assert json_str is not None
        parsed = json.loads(json_str)
        assert parsed["exception"]["type"] == "ValueError"

    def test_safe_success_has_no_traceback(self):
        """Safe with successful value has no captured_traceback."""
        from doeff.cesk import run_sync

        @do
        def program():
            result = yield Safe(succeeds())
            return result

        result = run_sync(program())
        value = result.value
        assert isinstance(value, Ok)


class TestSafeProgramInterpreter:
    """Test Safe effect with ProgramInterpreter."""

    @pytest.mark.asyncio
    async def test_safe_wraps_success_in_ok(self):
        """Safe wraps successful value in Ok."""
        from doeff.interpreter import ProgramInterpreter

        @do
        def program():
            result = yield Safe(succeeds())
            return result

        engine = ProgramInterpreter()
        result = await engine.run_async(program())
        assert result.is_ok
        value = result.value
        assert isinstance(value, Ok)
        assert value.value == 42

    @pytest.mark.asyncio
    async def test_safe_wraps_error_in_err(self):
        """Safe wraps error in Err."""
        from doeff.interpreter import ProgramInterpreter

        @do
        def program():
            result = yield Safe(fails_with_effect())
            return result

        engine = ProgramInterpreter()
        result = await engine.run_async(program())
        assert result.is_ok
        value = result.value
        assert isinstance(value, Err)
        assert isinstance(value.error, RuntimeError)

    @pytest.mark.asyncio
    async def test_safe_captures_python_traceback(self):
        """Safe captures PythonTraceback on error."""
        from doeff.interpreter import ProgramInterpreter
        from doeff.traceback import PythonTraceback

        @do
        def program():
            result = yield Safe(fails_with_effect())
            return result

        engine = ProgramInterpreter()
        result = await engine.run_async(program())
        assert result.is_ok
        value = result.value
        assert isinstance(value, Err)
        assert value.captured_traceback.is_some()
        trace = value.captured_traceback.unwrap()
        assert isinstance(trace, PythonTraceback)

    @pytest.mark.asyncio
    async def test_safe_python_traceback_format(self):
        """Safe PythonTraceback format works."""
        from doeff.interpreter import ProgramInterpreter

        @do
        def program():
            result = yield Safe(fails_with_effect())
            return result

        engine = ProgramInterpreter()
        result = await engine.run_async(program())
        value = result.value
        trace = value.captured_traceback.unwrap()
        formatted = trace.format()
        assert "RuntimeError" in formatted
        assert "effect failure" in formatted

    @pytest.mark.asyncio
    async def test_safe_python_traceback_format_short(self):
        """Safe PythonTraceback short format works."""
        from doeff.interpreter import ProgramInterpreter

        @do
        def program():
            result = yield Safe(fails_with_effect())
            return result

        engine = ProgramInterpreter()
        result = await engine.run_async(program())
        value = result.value
        trace = value.captured_traceback.unwrap()
        short = trace.format_short()
        assert "RuntimeError" in short

    @pytest.mark.asyncio
    async def test_safe_python_traceback_to_dict(self):
        """Safe PythonTraceback can be serialized."""
        from doeff.interpreter import ProgramInterpreter
        import json

        @do
        def program():
            result = yield Safe(fails_with_effect())
            return result

        engine = ProgramInterpreter()
        result = await engine.run_async(program())
        value = result.value
        trace = value.captured_traceback.unwrap()
        d = trace.to_dict()
        json_str = json.dumps(d)
        assert json_str is not None
        parsed = json.loads(json_str)
        assert parsed["exception"]["type"] == "RuntimeError"
        assert parsed["type"] == "python"


class TestSafeControlFlowClassification:
    """Test that ResultSafeEffect is properly classified as control flow."""

    def test_safe_is_control_flow_effect(self):
        """ResultSafeEffect should be classified as control flow effect."""
        from doeff.cesk import is_control_flow_effect
        from doeff.effects import ResultSafeEffect

        effect = ResultSafeEffect(sub_program=Program.pure(42))
        assert is_control_flow_effect(effect) is True
