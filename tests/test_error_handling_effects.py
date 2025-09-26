"""
Tests for Recover and Retry error handling effects.

These tests verify that the new error handling effects work correctly
and demonstrate why try/except doesn't work in @do functions.
"""


import pytest

from doeff import (
    IO,
    Catch,
    EffectGenerator,
    Fail,
    Get,
    Log,
    Local,
    Listen,
    ListenResult,
    Ok,
    Err,
    ProgramInterpreter,
    Put,
    Recover,
    Safe,
    Retry,
    do,
)


@pytest.mark.asyncio
async def test_recover_with_fallback_value():
    """Test Recover effect with simple fallback value."""

    @do
    def failing_program() -> EffectGenerator[int]:
        yield Log("About to fail")
        yield Fail(ValueError("Something went wrong"))
        return 42  # Never reached

    @do
    def program_with_recover() -> EffectGenerator[int]:
        # Recover from failure with fallback value
        result = yield Recover(failing_program(), fallback=100)
        yield Log(f"Recovered with value: {result}")
        return result

    engine = ProgramInterpreter()
    result = await engine.run(program_with_recover())

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
        # Recover with another program
        result = yield Recover(failing_program(), fallback=fallback_program())
        return result

    engine = ProgramInterpreter()
    result = await engine.run(main_program())

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
        # Recover shouldn't trigger on success
        result = yield Recover(successful_program(), fallback="fallback")
        yield Log(f"Got result: {result}")
        return result

    engine = ProgramInterpreter()
    result = await engine.run(main_program())

    assert result.is_ok
    assert result.value == "success"
    assert "Running successfully" in str(result.log[0])
    assert "Got result: success" in str(result.log[1])


@pytest.mark.asyncio
async def test_retry_success_on_second_attempt():
    """Test Retry effect succeeds after initial failure."""

    @do
    def flaky_program() -> EffectGenerator[int]:
        # Get attempt counter from state
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

    engine = ProgramInterpreter()
    result = await engine.run(main_program())

    assert result.is_ok
    assert result.value == 2
    assert result.state["attempt_count"] == 2
    # Check both attempts were logged
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

    engine = ProgramInterpreter()
    result = await engine.run(main_program())

    assert result.is_err
    # Should have attempted twice
    log_messages = [str(log) for log in result.log]
    attempt_count = sum(1 for msg in log_messages if "Attempting..." in msg)
    assert attempt_count == 2


@pytest.mark.asyncio
async def test_retry_with_delay():
    """Test Retry with delay between attempts."""
    import time

    @do
    def flaky_with_timing() -> EffectGenerator[float]:
        timestamp = time.time()
        yield Put("last_attempt", timestamp)

        # Get previous timestamp
        prev = yield Get("prev_timestamp")
        if prev:
            delay = timestamp - prev
            yield Log(f"Delay since last attempt: {delay:.3f}s")

        yield Put("prev_timestamp", timestamp)

        # Fail first time
        attempt = yield Get("retry_attempt")
        attempt = (attempt or 0) + 1
        yield Put("retry_attempt", attempt)

        if attempt < 2:
            yield Fail(RuntimeError("Not yet"))

        return timestamp

    @do
    def main_program() -> EffectGenerator[float]:
        result = yield Retry(flaky_with_timing(), max_attempts=2, delay_ms=100)
        return result

    engine = ProgramInterpreter()
    result = await engine.run(main_program())

    assert result.is_ok
    # Check that delay was applied (should be at least 100ms)
    log_messages = [str(log) for log in result.log]
    delay_logs = [msg for msg in log_messages if "Delay since last attempt" in msg]
    if delay_logs:
        # Extract delay value
        delay_str = delay_logs[0].split(": ")[1].replace("s", "")
        delay = float(delay_str)
        assert delay >= 0.1  # At least 100ms


@pytest.mark.asyncio
async def test_nested_error_handling():
    """Test nested Recover and Retry effects."""

    @do
    def deeply_nested() -> EffectGenerator[str]:
        yield Fail(RuntimeError("Deep error"))
        return "never"

    @do
    def middle_layer() -> EffectGenerator[str]:
        # Try with Recover
        result = yield Recover(deeply_nested(), fallback="recovered")
        return f"middle: {result}"

    @do
    def outer_layer() -> EffectGenerator[str]:
        # Retry the middle layer (which always succeeds due to Recover)
        result = yield Retry(middle_layer(), max_attempts=2)
        return f"outer: {result}"

    engine = ProgramInterpreter()
    result = await engine.run(outer_layer())

    assert result.is_ok
    assert result.value == "outer: middle: recovered"


@pytest.mark.asyncio
async def test_catch_vs_recover():
    """Test difference between Catch and Recover."""

    @do
    def failing() -> EffectGenerator[int]:
        yield Fail(ValueError("test error"))
        return 42

    @do
    def test_catch() -> EffectGenerator[str]:
        # Catch allows handling the error
        result = yield Catch(
            failing(),
            lambda e: f"Caught: {e}"
        )
        return result

    @do
    def test_recover() -> EffectGenerator[str]:
        # Recover just provides fallback
        result = yield Recover(failing(), fallback="fallback")
        return result

    engine = ProgramInterpreter()

    # Test Catch
    catch_result = await engine.run(test_catch())
    assert catch_result.is_ok
    assert "Caught: test error" in catch_result.value

    # Test Recover
    recover_result = await engine.run(test_recover())
    assert recover_result.is_ok
    assert recover_result.value == "fallback"


@pytest.mark.asyncio
async def test_catch_handler_logs_are_accumulated():
    """Catch handler logs should append to the surrounding writer log."""

    @do
    def failing_program() -> EffectGenerator[int]:
        yield Log("before fail")
        yield Fail(ValueError("boom"))
        return 0  # Never reached

    @do
    def handler(exc: Exception) -> EffectGenerator[int]:
        yield Log(f"handled {type(exc).__name__}")
        return 42

    @do
    def main_program() -> EffectGenerator[int]:
        result = yield Catch(failing_program(), handler)
        return result

    engine = ProgramInterpreter()
    result = await engine.run(main_program())

    assert result.is_ok
    assert result.value == 42
    assert result.log == ["before fail", "handled ValueError"]


@pytest.mark.asyncio
async def test_catch_handler_logs_with_listen():
    """Listen should capture logs produced by both failing program and handler."""

    @do
    def failing_program() -> EffectGenerator[int]:
        yield Log("before fail")
        yield Fail(RuntimeError("nope"))
        return 0

    @do
    def handler(exc: Exception) -> EffectGenerator[int]:
        yield Log(f"handled {type(exc).__name__}")
        return 7

    @do
    def main_program() -> EffectGenerator:
        listen_result = yield Listen(Catch(failing_program(), handler))
        return listen_result

    engine = ProgramInterpreter()
    result = await engine.run(main_program())

    assert result.is_ok
    assert isinstance(result.value, ListenResult)
    assert result.value.value == 7
    assert result.value.log == ["before fail", "handled RuntimeError"]
    assert result.log == []  # Listen isolates the log from the parent context


@pytest.mark.asyncio
async def test_catch_handler_fail_propagates_error_logs():
    """If the handler fails, logs before the failure should persist once."""

    @do
    def failing_program() -> EffectGenerator[int]:
        yield Log("before fail")
        yield Fail(ValueError("boom"))

    @do
    def failing_handler(exc: Exception) -> EffectGenerator[int]:
        yield Log(f"handled {type(exc).__name__}")
        yield Fail(RuntimeError("handler boom"))

    @do
    def main_program() -> EffectGenerator[None]:
        yield Catch(failing_program(), failing_handler)

    engine = ProgramInterpreter()
    result = await engine.run(main_program())

    assert result.is_err

    error = result.result.error
    from doeff.types import EffectFailure

    while isinstance(error, EffectFailure):
        error = error.cause

    assert isinstance(error, RuntimeError)
    assert str(error) == "handler boom"
    assert result.log == ["before fail", "handled ValueError"]


@pytest.mark.asyncio
async def test_catch_within_local_propagates_logs():
    """Catch inside Local should retain logs when rethrown from handler."""

    @do
    def failing_subprogram() -> EffectGenerator[int]:
        yield Log("sub before fail")
        yield Fail(ValueError("boom"))

    @do
    def failing_handler(exc: Exception) -> EffectGenerator[int]:
        yield Log(f"handler saw {exc}")
        yield Fail(exc)

    @do
    def main_program() -> EffectGenerator[int]:
        yield Local({}, Catch(failing_subprogram(), failing_handler))
        return 0

    engine = ProgramInterpreter()
    result = await engine.run(main_program())

    assert result.is_err

    error = result.result.error
    from doeff.types import EffectFailure

    while isinstance(error, EffectFailure):
        error = error.cause

    assert isinstance(error, ValueError)
    assert result.log == ["sub before fail", "handler saw boom"]


@pytest.mark.asyncio
async def test_retry_of_catch_preserves_attempt_logs():
    """Retry should keep logs from each attempt when handlers rethrow."""

    attempts: list[int] = []

    @do
    def unstable() -> EffectGenerator[int]:
        attempt_no = len(attempts) + 1
        attempts.append(attempt_no)
        yield Log(f"attempt {attempt_no}")
        yield Fail(ValueError(f"boom {attempt_no}"))

    @do
    def handler(exc: Exception) -> EffectGenerator[int]:
        yield Log(f"handler saw {exc}")
        yield Fail(exc)

    @do
    def main_program() -> EffectGenerator[int]:
        yield Retry(Catch(unstable(), handler), max_attempts=2, delay_ms=0)
        return 0

    engine = ProgramInterpreter()
    result = await engine.run(main_program())

    assert result.is_err

    error = result.result.error
    from doeff.types import EffectFailure

    while isinstance(error, EffectFailure):
        error = error.cause

    assert isinstance(error, ValueError)
    assert str(error) == "boom 2"
    assert result.log == [
        "attempt 1",
        "handler saw boom 1",
        "attempt 2",
        "handler saw boom 2",
    ]


@pytest.mark.asyncio
async def test_why_try_except_doesnt_work():
    """
    Demonstrate why try/except doesn't work in @do functions.
    
    This test shows that exceptions from yielded effects are NOT
    caught by try/except blocks in generator functions.
    """

    @do
    def program_with_incorrect_try_except() -> EffectGenerator[str]:
        # THIS PATTERN DOES NOT WORK!
        # The try/except will NOT catch exceptions from the yielded effect

        # This is what users might incorrectly try to do:
        # try:
        #     value = yield Fail(ValueError("This error is not caught"))
        #     return value
        # except ValueError:
        #     return "caught"  # This will NEVER execute

        # Instead, we must use effect-based error handling:
        value = yield Recover(
            Fail(ValueError("This error IS properly handled")),
            fallback="properly recovered"
        )
        return value

    engine = ProgramInterpreter()
    result = await engine.run(program_with_incorrect_try_except())

    assert result.is_ok
    assert result.value == "properly recovered"


@pytest.mark.asyncio
async def test_recover_with_io_effect():
    """Test Recover with IO effects that might fail."""

    @do
    def risky_io() -> EffectGenerator[str]:
        # IO operation that fails
        def failing_io():
            raise OSError("Disk full")

        result = yield IO(failing_io)
        return result

    @do
    def safe_io() -> EffectGenerator[str]:
        # Recover from IO failure
        result = yield Recover(
            risky_io(),
            fallback="default_content"
        )
        yield Log(f"IO result: {result}")
        return result

    engine = ProgramInterpreter()
    result = await engine.run(safe_io())

    assert result.is_ok
    assert result.value == "default_content"
    assert "IO result: default_content" in str(result.log[0])


@pytest.mark.asyncio
async def test_safe_wraps_successful_program() -> None:
    """Safe should return Ok when the sub-program succeeds."""

    @do
    def happy_program() -> EffectGenerator[int]:
        return 42

    @do
    def main_program() -> EffectGenerator[Ok[int]]:
        outcome = yield Safe(happy_program())
        assert isinstance(outcome, Ok)
        return outcome

    engine = ProgramInterpreter()
    run_result = await engine.run(main_program())

    assert run_result.is_ok
    assert isinstance(run_result.value, Ok)
    assert run_result.value.value == 42


@pytest.mark.asyncio
async def test_safe_wraps_failing_program() -> None:
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

    engine = ProgramInterpreter()
    run_result = await engine.run(main_program())

    assert run_result.is_ok
    assert isinstance(run_result.value, Err)
    assert isinstance(run_result.value.error, ValueError)
    assert str(run_result.value.error) == "boom"
    from doeff.types import EffectFailure

    assert not isinstance(run_result.value.error, EffectFailure)


if __name__ == "__main__":
    # Run the tests
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
