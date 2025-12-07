"""
Tests for Recover and Retry error handling effects.

These tests verify that the new error handling effects work correctly
and demonstrate why try/except doesn't work in @do functions.
"""

import asyncio
import pytest

from doeff import (
    IO,
    Catch,
    EffectGenerator,
    Err,
    Fail,
    Finally,
    Get,
    Listen,
    ListenResult,
    Local,
    Log,
    Ok,
    Program,
    ProgramInterpreter,
    Put,
    Recover,
    Retry,
    Safe,
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
        # Recover with another program
        result = yield Recover(failing_program(), fallback=fallback_program())
        return result

    engine = ProgramInterpreter()
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
        # Recover shouldn't trigger on success
        result = yield Recover(successful_program(), fallback="fallback")
        yield Log(f"Got result: {result}")
        return result

    engine = ProgramInterpreter()
    result = await engine.run_async(main_program())

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
    result = await engine.run_async(main_program())

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
    result = await engine.run_async(main_program())

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
    result = await engine.run_async(main_program())

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
async def test_retry_with_delay_strategy(monkeypatch):
    """Retry should use delay_strategy when provided."""

    sleep_calls: list[float] = []

    async def fake_sleep(duration: float):
        sleep_calls.append(duration)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    recorded_attempts: list[tuple[int, Exception | None]] = []

    def delay_strategy(attempt: int, error: Exception | None) -> float:
        recorded_attempts.append((attempt, error))
        return attempt * 0.05

    @do
    def flaky_program() -> EffectGenerator[int]:
        attempt = yield Get("attempt")
        attempt = (attempt or 0) + 1
        yield Put("attempt", attempt)
        if attempt < 3:
            yield Fail(RuntimeError(f"attempt-{attempt} failure"))
        return attempt

    @do
    def main_program() -> EffectGenerator[int]:
        result = yield Retry(
            flaky_program(),
            max_attempts=3,
            delay_strategy=delay_strategy,
        )
        return result

    engine = ProgramInterpreter()
    result = await engine.run_async(main_program())

    assert result.is_ok
    assert result.value == 3
    assert sleep_calls == pytest.approx([0.05, 0.10])
    assert [attempt for attempt, _ in recorded_attempts] == [1, 2]
    assert all("attempt-" in str(error) for _, error in recorded_attempts)


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
    result = await engine.run_async(outer_layer())

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
    catch_result = await engine.run_async(test_catch())
    assert catch_result.is_ok
    assert "Caught: test error" in catch_result.value

    # Test Recover
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
    result = await engine.run_async(main_program())

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
    result = await engine.run_async(main_program())

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
    result = await engine.run_async(main_program())

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
    result = await engine.run_async(main_program())

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
    result = await engine.run_async(main_program())

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
    result = await engine.run_async(program_with_incorrect_try_except())

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
    result = await engine.run_async(safe_io())

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
    run_result = await engine.run_async(main_program())

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
    run_result = await engine.run_async(main_program())

    assert run_result.is_ok
    assert isinstance(run_result.value, Err)
    assert isinstance(run_result.value.error, ValueError)
    assert str(run_result.value.error) == "boom"
    from doeff.types import EffectFailure

    assert not isinstance(run_result.value.error, EffectFailure)


@pytest.mark.asyncio
async def test_finally_runs_finalizer_on_success() -> None:
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

    engine = ProgramInterpreter()
    run_result = await engine.run_async(program())

    assert run_result.is_ok
    assert run_result.value == 7
    # Logs should include finalizer output before the trailing log
    assert str(run_result.log[0]) == "inner start"
    assert str(run_result.log[1]) == "cleanup complete"
    assert str(run_result.log[2]) == "after finally 7"


@pytest.mark.asyncio
async def test_finally_runs_on_failure() -> None:
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

    engine = ProgramInterpreter()
    run_result = await engine.run_async(program())

    assert run_result.is_err
    assert cleanup == ["ran"]
    assert str(run_result.log[0]) == "about to fail"


@pytest.mark.asyncio
async def test_finally_callable_returning_effect_runs() -> None:
    """Callable finalizers returning effects should be executed."""

    @do
    def inner() -> EffectGenerator[int]:
        return 1

    @do
    def program() -> EffectGenerator[int]:
        value = yield Finally(inner(), lambda: Log("callable finalizer"))
        return value

    engine = ProgramInterpreter()
    run_result = await engine.run_async(program())

    assert run_result.is_ok
    assert run_result.value == 1
    assert str(run_result.log[0]) == "callable finalizer"


# =============================================================================
# Native try-except tests (GitHub Issue #2)
# =============================================================================


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

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "caught: test error"


@pytest.mark.asyncio
async def test_native_try_except_catches_subprogram_error():
    """Native try-except should catch errors from sub-programs."""

    @do
    def failing_subprogram() -> EffectGenerator[int]:
        yield Program.pure(1)
        raise ValueError("subprogram error")

    @do
    def program() -> EffectGenerator[str]:
        try:
            x = yield failing_subprogram()
            return f"got: {x}"
        except ValueError as e:
            return f"caught: {e}"

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "caught: subprogram error"


@pytest.mark.asyncio
async def test_nested_try_except():
    """Nested try-except blocks should work correctly."""

    @do
    def program() -> EffectGenerator[str]:
        try:
            try:
                yield Fail(ValueError("inner error"))
                return "unreachable"
            except TypeError:
                return "caught TypeError"
        except ValueError as e:
            return f"caught ValueError: {e}"

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "caught ValueError: inner error"


@pytest.mark.asyncio
async def test_try_finally_executes_on_exception():
    """finally block should execute when exception is caught."""

    cleanup_executed = []

    @do
    def program() -> EffectGenerator[str]:
        try:
            yield Fail(ValueError("error"))
            return "unreachable"
        except ValueError:
            return "caught"
        finally:
            cleanup_executed.append(True)

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "caught"
    assert cleanup_executed == [True]


@pytest.mark.asyncio
async def test_uncaught_exception_becomes_err():
    """Uncaught exceptions should still become Err results."""

    @do
    def program() -> EffectGenerator[str]:
        try:
            yield Fail(ValueError("test error"))
            return "unreachable"
        except TypeError:
            # Only catching TypeError, not ValueError
            return "caught TypeError"

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_err
    # The error should be ValueError since it wasn't caught
    assert isinstance(result.result.error.cause, ValueError)


@pytest.mark.asyncio
async def test_exception_reraise():
    """Re-raised exceptions should propagate correctly."""

    @do
    def program() -> EffectGenerator[str]:
        try:
            yield Fail(ValueError("original"))
        except ValueError:
            raise RuntimeError("re-raised")

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_err
    # Should have the new RuntimeError
    assert isinstance(result.result.error.cause, RuntimeError)
    assert str(result.result.error.cause) == "re-raised"


@pytest.mark.asyncio
async def test_safe_catch_recover_still_work():
    """Effect-based error handling should still work alongside try-except."""

    @do
    def program() -> EffectGenerator[str]:
        # Effect-based handling
        value1 = yield Recover(
            Fail(ValueError("error1")),
            fallback="recovered1"
        )

        # Native try-except
        try:
            yield Fail(ValueError("error2"))
            value2 = "unreachable"
        except ValueError:
            value2 = "caught2"

        return f"{value1}, {value2}"

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "recovered1, caught2"


@pytest.mark.asyncio
async def test_try_except_with_multiple_yields():
    """try-except should work with multiple yields inside."""

    @do
    def program() -> EffectGenerator[str]:
        results = []
        try:
            results.append((yield Program.pure(1)))
            results.append((yield Program.pure(2)))
            yield Fail(ValueError("after yields"))
        except ValueError:
            return f"caught after {results}"

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "caught after [1, 2]"


# =============================================================================
# Comprehensive tests: try-except with various effect combinations
# =============================================================================


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

    engine = ProgramInterpreter()
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

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "handled"
    log_messages = [str(entry) for entry in result.log]
    assert "before try" in log_messages[0]
    assert "inside try" in log_messages[1]
    assert "caught: error" in log_messages[2]


@pytest.mark.asyncio
async def test_try_except_with_io_effect():
    """try-except should work with IO effects that fail."""

    @do
    def program() -> EffectGenerator[str]:
        def failing_io() -> None:
            raise OSError("disk full")

        try:
            yield IO(failing_io)
            return "unreachable"
        except OSError as e:
            return f"caught IO error: {e}"

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "caught IO error: disk full"


@pytest.mark.asyncio
async def test_try_except_with_nested_subprograms():
    """try-except should catch errors from deeply nested subprograms."""

    @do
    def level3() -> EffectGenerator[int]:
        yield Log("level3")
        raise ValueError("deep error")

    @do
    def level2() -> EffectGenerator[int]:
        yield Log("level2")
        return (yield level3())

    @do
    def level1() -> EffectGenerator[int]:
        yield Log("level1")
        return (yield level2())

    @do
    def program() -> EffectGenerator[str]:
        try:
            value = yield level1()
            return f"got: {value}"
        except ValueError as e:
            return f"caught from nested: {e}"

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "caught from nested: deep error"


@pytest.mark.asyncio
async def test_try_except_with_recover_fallback():
    """try-except and Recover should work together."""

    @do
    def program() -> EffectGenerator[str]:
        # First, use Recover for one error
        val1 = yield Recover(
            Fail(ValueError("error1")),
            fallback="recovered1"
        )

        # Then, use try-except for another error
        try:
            yield Fail(ValueError("error2"))
            val2 = "unreachable"
        except ValueError:
            val2 = "caught2"

        # Finally, another Recover
        val3 = yield Recover(
            Fail(ValueError("error3")),
            fallback="recovered3"
        )

        return f"{val1}, {val2}, {val3}"

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "recovered1, caught2, recovered3"


@pytest.mark.asyncio
async def test_try_except_inside_catch_handler():
    """try-except should work inside a Catch handler."""

    @do
    def error_handler(e: Exception) -> EffectGenerator[str]:
        try:
            yield Fail(RuntimeError("handler error"))
            return "unreachable"
        except RuntimeError:
            return f"handler caught its own error, original: {e}"

    @do
    def program() -> EffectGenerator[str]:
        result = yield Catch(
            Fail(ValueError("original error")),
            error_handler
        )
        return result

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert "handler caught its own error" in result.value
    assert "original error" in result.value


@pytest.mark.asyncio
async def test_try_except_with_finally_and_effects():
    """try-except-finally should work with effects in finally block."""

    cleanup_log = []

    @do
    def program() -> EffectGenerator[str]:
        try:
            yield Log("in try")
            yield Fail(ValueError("error"))
            return "unreachable"
        except ValueError:
            yield Log("in except")
            return "caught"
        finally:
            cleanup_log.append("finally executed")
            # Note: yields in finally are tricky, so we use a side effect here

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "caught"
    assert cleanup_log == ["finally executed"]


@pytest.mark.asyncio
async def test_try_except_preserves_context():
    """try-except should preserve execution context across error handling."""

    @do
    def program() -> EffectGenerator[str]:
        yield Put("before_try", True)

        try:
            yield Put("in_try", True)
            yield Fail(ValueError("error"))
        except ValueError:
            yield Put("in_except", True)
            before = yield Get("before_try")
            in_try = yield Get("in_try")
            return f"before={before}, in_try={in_try}"

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "before=True, in_try=True"
    assert result.state.get("in_except") is True


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

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "caught1, caught2, caught3"


@pytest.mark.asyncio
async def test_try_except_with_safe_effect():
    """try-except should work alongside Safe effect."""

    @do
    def program() -> EffectGenerator[str]:
        # Safe wraps the result in Ok/Err
        safe_result = yield Safe(Fail(ValueError("safe error")))
        assert isinstance(safe_result, Err)

        # try-except catches the error directly
        try:
            yield Fail(ValueError("try error"))
            direct_result = "unreachable"
        except ValueError:
            direct_result = "caught directly"

        return f"safe={type(safe_result).__name__}, direct={direct_result}"

    engine = ProgramInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "safe=Err, direct=caught directly"


if __name__ == "__main__":
    # Run the tests
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
