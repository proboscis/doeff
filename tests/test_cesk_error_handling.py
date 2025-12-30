"""
CESK interpreter tests for error handling effects.

This module tests Recover/Retry/Catch/Safe/Finally effects using the CESK
interpreter, adapted from test_error_handling_effects.py.
"""

import pytest

from doeff import (
    Catch,
    EffectGenerator,
    Fail,
    Finally,
    Get,
    Listen,
    ListenResult,
    Local,
    Log,
    Program,
    Put,
    Recover,
    Retry,
    Safe,
    do,
)
from doeff._vendor import Err, Ok
from doeff.cesk_adapter import CESKInterpreter


# ============================================================================
# Recover Effect Tests
# ============================================================================


@pytest.mark.asyncio
async def test_recover_with_fallback_value():
    """Test Recover effect with simple fallback value."""

    @do
    def failing_program() -> EffectGenerator[int]:
        yield Log("About to fail")
        yield Fail(ValueError("Something went wrong"))
        return 42

    @do
    def program_with_recover() -> EffectGenerator[int]:
        result = yield Recover(failing_program(), fallback=100)
        yield Log(f"Recovered with value: {result}")
        return result

    engine = CESKInterpreter()
    result = await engine.run_async(program_with_recover())

    assert result.is_ok
    assert result.value == 100
    assert "About to fail" in str(result.log[0])
    assert "Recovered with value: 100" in str(result.log[1])


@pytest.mark.asyncio
async def test_recover_with_fallback_program():
    """Test Recover effect with fallback program."""

    @do
    def failing_program() -> EffectGenerator[str]:
        yield Fail(RuntimeError("Failed"))
        return "never"

    @do
    def fallback_program() -> EffectGenerator[str]:
        yield Log("Using fallback")
        return "fallback_value"

    @do
    def main_program() -> EffectGenerator[str]:
        result = yield Recover(failing_program(), fallback=fallback_program())
        return result

    engine = CESKInterpreter()
    result = await engine.run_async(main_program())

    assert result.is_ok
    assert result.value == "fallback_value"
    assert "Using fallback" in str(result.log[0])


@pytest.mark.asyncio
async def test_recover_on_success():
    """Test Recover doesn't use fallback on success."""

    @do
    def successful_program() -> EffectGenerator[str]:
        yield Log("Running successfully")
        return "success"

    @do
    def main_program() -> EffectGenerator[str]:
        result = yield Recover(successful_program(), fallback="fallback")
        yield Log(f"Got result: {result}")
        return result

    engine = CESKInterpreter()
    result = await engine.run_async(main_program())

    assert result.is_ok
    assert result.value == "success"
    assert "Running successfully" in str(result.log[0])
    assert "Got result: success" in str(result.log[1])


# ============================================================================
# Retry Effect Tests
# ============================================================================


@pytest.mark.asyncio
async def test_retry_success_on_second_attempt():
    """Test Retry effect succeeds after initial failure."""

    @do
    def flaky_program() -> EffectGenerator[int]:
        attempt = yield Get("attempt_count")
        attempt = (attempt or 0) + 1
        yield Put("attempt_count", attempt)
        yield Log(f"Attempt {attempt}")

        if attempt < 2:
            yield Fail(RuntimeError(f"Failed on attempt {attempt}"))

        return attempt

    @do
    def main_program() -> EffectGenerator[int]:
        result = yield Retry(flaky_program(), max_attempts=3)
        yield Log(f"Succeeded with result: {result}")
        return result

    engine = CESKInterpreter()
    result = await engine.run_async(main_program())

    assert result.is_ok
    assert result.value == 2
    assert result.state["attempt_count"] == 2
    assert "Attempt 1" in str(result.log[0])
    assert "Attempt 2" in str(result.log[1])
    assert "Succeeded with result: 2" in str(result.log[2])


@pytest.mark.asyncio
async def test_retry_max_attempts_exceeded():
    """Test Retry fails after max attempts."""

    @do
    def always_failing() -> EffectGenerator[int]:
        yield Log("Attempting...")
        yield Fail(ValueError("Always fails"))
        return 42

    @do
    def main_program() -> EffectGenerator[int]:
        result = yield Retry(always_failing(), max_attempts=2)
        return result

    engine = CESKInterpreter()
    result = await engine.run_async(main_program())

    assert result.is_err
    log_messages = [str(log) for log in result.log]
    attempt_count = sum(1 for msg in log_messages if "Attempting..." in msg)
    assert attempt_count == 2


# ============================================================================
# Catch Effect Tests
# ============================================================================


@pytest.mark.asyncio
async def test_catch_vs_recover():
    """Test difference between Catch and Recover."""

    @do
    def failing() -> EffectGenerator[int]:
        yield Fail(ValueError("test error"))
        return 42

    @do
    def test_catch() -> EffectGenerator[str]:
        result = yield Catch(
            failing(),
            lambda e: f"Caught: {e}"
        )
        return result

    @do
    def test_recover() -> EffectGenerator[str]:
        result = yield Recover(failing(), fallback="fallback")
        return result

    engine = CESKInterpreter()

    catch_result = await engine.run_async(test_catch())
    assert catch_result.is_ok
    assert "Caught: test error" in catch_result.value

    recover_result = await engine.run_async(test_recover())
    assert recover_result.is_ok
    assert recover_result.value == "fallback"


@pytest.mark.asyncio
async def test_catch_handler_logs_are_accumulated():
    """Catch handler logs should append to the surrounding writer log."""

    @do
    def failing_program() -> EffectGenerator[int]:
        yield Log("before fail")
        yield Fail(ValueError("boom"))
        return 0

    @do
    def handler(exc: Exception) -> EffectGenerator[int]:
        yield Log(f"handled {type(exc).__name__}")
        return 42

    @do
    def main_program() -> EffectGenerator[int]:
        result = yield Catch(failing_program(), handler)
        return result

    engine = CESKInterpreter()
    result = await engine.run_async(main_program())

    assert result.is_ok
    assert result.value == 42
    assert result.log == ["before fail", "handled ValueError"]


@pytest.mark.asyncio
async def test_nested_error_handling():
    """Test nested Recover and Retry effects."""

    @do
    def deeply_nested() -> EffectGenerator[str]:
        yield Fail(RuntimeError("Deep error"))
        return "never"

    @do
    def middle_layer() -> EffectGenerator[str]:
        result = yield Recover(deeply_nested(), fallback="recovered")
        return f"middle: {result}"

    @do
    def outer_layer() -> EffectGenerator[str]:
        result = yield Retry(middle_layer(), max_attempts=2)
        return f"outer: {result}"

    engine = CESKInterpreter()
    result = await engine.run_async(outer_layer())

    assert result.is_ok
    assert result.value == "outer: middle: recovered"


# ============================================================================
# Safe Effect Tests
# ============================================================================


@pytest.mark.asyncio
async def test_safe_wraps_successful_program():
    """Safe should return Ok when the sub-program succeeds."""

    @do
    def happy_program() -> EffectGenerator[int]:
        return 42

    @do
    def main_program() -> EffectGenerator[Ok[int]]:
        outcome = yield Safe(happy_program())
        assert isinstance(outcome, Ok)
        return outcome

    engine = CESKInterpreter()
    run_result = await engine.run_async(main_program())

    assert run_result.is_ok
    assert isinstance(run_result.value, Ok)
    assert run_result.value.value == 42


@pytest.mark.asyncio
async def test_safe_wraps_failing_program():
    """Safe should return Err when the sub-program fails."""

    @do
    def failing_program() -> EffectGenerator[int]:
        yield Fail(ValueError("boom"))
        return 0

    @do
    def main_program() -> EffectGenerator[Err]:
        outcome = yield Safe(failing_program())
        assert isinstance(outcome, Err)
        return outcome

    engine = CESKInterpreter()
    run_result = await engine.run_async(main_program())

    assert run_result.is_ok
    assert isinstance(run_result.value, Err)
    assert isinstance(run_result.value.error, ValueError)
    assert str(run_result.value.error) == "boom"


# ============================================================================
# Finally Effect Tests
# ============================================================================


@pytest.mark.asyncio
async def test_finally_runs_finalizer_on_success():
    """Finally executes the finalizer program when the sub-program succeeds."""

    @do
    def inner() -> EffectGenerator[int]:
        yield Log("inner start")
        return 7

    @do
    def finalizer() -> EffectGenerator[None]:
        yield Log("cleanup complete")
        return None

    @do
    def program() -> EffectGenerator[int]:
        result = yield Finally(inner(), finalizer())
        yield Log(f"after finally {result}")
        return result

    engine = CESKInterpreter()
    run_result = await engine.run_async(program())

    assert run_result.is_ok
    assert run_result.value == 7
    assert str(run_result.log[0]) == "inner start"
    assert str(run_result.log[1]) == "cleanup complete"
    assert str(run_result.log[2]) == "after finally 7"


@pytest.mark.asyncio
async def test_finally_runs_on_failure():
    """Finally executes the finalizer even when the sub-program fails."""

    cleanup: list[str] = []

    def finalizer_callable() -> None:
        cleanup.append("ran")

    @do
    def failing() -> EffectGenerator[None]:
        yield Log("about to fail")
        yield Fail(RuntimeError("boom"))

    @do
    def program() -> EffectGenerator[None]:
        yield Finally(failing(), finalizer_callable)
        return None

    engine = CESKInterpreter()
    run_result = await engine.run_async(program())

    assert run_result.is_err
    assert cleanup == ["ran"]
    assert str(run_result.log[0]) == "about to fail"


# ============================================================================
# Native try-except Tests
# ============================================================================


@pytest.mark.asyncio
async def test_native_try_except_catches_effect_error():
    """Native try-except should catch errors from yielded effects."""

    @do
    def program() -> EffectGenerator[str]:
        try:
            yield Fail(ValueError("test error"))
            return "unreachable"
        except ValueError as e:
            return f"caught: {e}"

    engine = CESKInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "caught: test error"


@pytest.mark.asyncio
async def test_try_except_with_state_effects():
    """try-except should work alongside state effects (Get/Put)."""

    @do
    def program() -> EffectGenerator[str]:
        yield Put("counter", 0)

        try:
            yield Put("counter", 1)
            yield Fail(ValueError("after state change"))
        except ValueError:
            counter = yield Get("counter")
            return f"caught, counter={counter}"

    engine = CESKInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "caught, counter=1"
    assert result.state.get("counter") == 1


@pytest.mark.asyncio
async def test_try_except_with_log_effects():
    """try-except should work alongside log effects."""

    @do
    def program() -> EffectGenerator[str]:
        yield Log("before try")

        try:
            yield Log("inside try")
            yield Fail(ValueError("error"))
        except ValueError as e:
            yield Log(f"caught: {e}")
            return "handled"

    engine = CESKInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "handled"
    log_messages = [str(entry) for entry in result.log]
    assert "before try" in log_messages[0]
    assert "inside try" in log_messages[1]
    assert "caught: error" in log_messages[2]


@pytest.mark.asyncio
async def test_multiple_try_except_blocks():
    """Multiple sequential try-except blocks should all work."""

    @do
    def program() -> EffectGenerator[str]:
        results = []

        try:
            yield Fail(ValueError("error1"))
        except ValueError:
            results.append("caught1")

        try:
            yield Fail(TypeError("error2"))
        except TypeError:
            results.append("caught2")

        try:
            yield Fail(RuntimeError("error3"))
        except RuntimeError:
            results.append("caught3")

        return ", ".join(results)

    engine = CESKInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "caught1, caught2, caught3"


@pytest.mark.asyncio
async def test_try_except_with_safe_effect():
    """try-except should work alongside Safe effect."""

    @do
    def program() -> EffectGenerator[str]:
        safe_result = yield Safe(Fail(ValueError("safe error")))
        assert isinstance(safe_result, Err)

        try:
            yield Fail(ValueError("try error"))
            direct_result = "unreachable"
        except ValueError:
            direct_result = "caught directly"

        return f"safe={type(safe_result).__name__}, direct={direct_result}"

    engine = CESKInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "safe=Err, direct=caught directly"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
