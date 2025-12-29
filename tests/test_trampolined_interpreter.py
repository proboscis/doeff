"""
Comprehensive tests for TrampolinedInterpreter.

These tests verify:
1. Core types (ContinuationFrame, FrameState, StepActions)
2. Basic interpreter functionality
3. Deep recursion/stack safety (10K+ depth)
4. Error handling and propagation
5. Effect stack trace building
6. All effect categories
7. Performance characteristics
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Generator
from typing import Any

import pytest

from doeff import (
    Effect,
    ExecutionContext,
    Program,
    ask,
    atomic_get,
    atomic_update,
    await_,
    catch,
    fail,
    finally_,
    Gather,
    get,
    io,
    listen,
    local,
    modify,
    parallel,
    put,
    recover,
    safe,
    step,
    tell,
)
from doeff.effects import gather
from doeff.do import do
from doeff.interpreter_v2 import (
    ContinuationFrame,
    ContinuationStackOverflowError,
    EffectStackFrame,
    EffectStackFrameType,
    EffectStackTrace,
    EffectStackTraceRenderer,
    FrameResultRaise,
    FrameResultReturn,
    FrameResultYield,
    FrameState,
    InterpretationPhase,
    InterpretationStats,
    InterpreterInvariantError,
    InterpreterState,
    InterpreterStateSnapshot,
    InvalidFrameStateError,
    PythonLocation,
    StepActionContinue,
    StepActionDone,
    StepActionError,
    StepActionYieldEffect,
    TrampolinedInterpreter,
)
from doeff.program import GeneratorProgram


# ============================================
# Unit Tests: Core Types
# ============================================


class TestFrameState:
    """Test FrameState enum."""

    def test_all_states_exist(self):
        assert FrameState.ACTIVE
        assert FrameState.COMPLETED
        assert FrameState.FAILED
        assert FrameState.CANCELLED


class TestContinuationFrame:
    """Test ContinuationFrame operations."""

    def test_resume_returns_yield_result(self):
        def gen():
            yield "effect1"
            return "done"

        frame = ContinuationFrame(
            generator=gen(),
            source_info=None,
        )

        result = frame.resume(None)
        assert isinstance(result, FrameResultYield)
        assert result.item == "effect1"
        assert frame.state == FrameState.ACTIVE

    def test_resume_returns_return_result(self):
        def gen():
            if False:  # Make it a generator
                yield
            return "done"

        frame = ContinuationFrame(
            generator=gen(),
            source_info=None,
        )

        # Start the generator
        result = frame.resume(None)
        assert isinstance(result, FrameResultReturn)
        assert result.value == "done"
        assert frame.state == FrameState.COMPLETED

    def test_resume_returns_raise_result(self):
        def gen():
            raise ValueError("test error")
            yield  # Make it a generator

        frame = ContinuationFrame(
            generator=gen(),
            source_info=None,
        )

        result = frame.resume(None)
        assert isinstance(result, FrameResultRaise)
        assert isinstance(result.exception, ValueError)
        assert frame.state == FrameState.FAILED

    def test_throw_caught_by_generator(self):
        def gen():
            try:
                yield "before"
            except ValueError:
                yield "caught"
            return "done"

        frame = ContinuationFrame(
            generator=gen(),
            source_info=None,
        )

        # Start generator
        result = frame.resume(None)
        assert isinstance(result, FrameResultYield)
        assert result.item == "before"

        # Throw exception
        result = frame.throw(ValueError("test"))
        assert isinstance(result, FrameResultYield)
        assert result.item == "caught"

    def test_throw_propagates_uncaught(self):
        def gen():
            yield "before"
            return "done"

        frame = ContinuationFrame(
            generator=gen(),
            source_info=None,
        )

        # Start generator
        frame.resume(None)

        # Throw exception
        result = frame.throw(ValueError("test"))
        assert isinstance(result, FrameResultRaise)
        assert isinstance(result.exception, ValueError)
        assert frame.state == FrameState.FAILED

    def test_cannot_resume_completed_frame(self):
        def gen():
            if False:
                yield
            return "done"

        frame = ContinuationFrame(
            generator=gen(),
            source_info=None,
        )
        frame.resume(None)  # Complete the frame

        with pytest.raises(InvalidFrameStateError):
            frame.resume("value")

    def test_close_cancels_frame(self):
        def gen():
            yield "first"
            yield "second"

        frame = ContinuationFrame(
            generator=gen(),
            source_info=None,
        )
        frame.resume(None)
        frame.close()

        assert frame.state == FrameState.CANCELLED


class TestStepActions:
    """Test StepAction types."""

    def test_step_action_continue(self):
        action = StepActionContinue()
        assert isinstance(action, StepActionContinue)

    def test_step_action_yield_effect(self):
        effect = put("key", "value")
        action = StepActionYieldEffect(effect=effect)
        assert action.effect == effect

    def test_step_action_done(self):
        action = StepActionDone(value=42)
        assert action.value == 42

    def test_step_action_error(self):
        exc = ValueError("test")
        action = StepActionError(exception=exc, stack_snapshot=())
        assert action.exception == exc


class TestInterpreterState:
    """Test InterpreterState management."""

    def test_push_pop_frame(self):
        state = InterpreterState(
            continuation_stack=[],
            current_item=None,
            context=ExecutionContext(),
            phase=InterpretationPhase.STEPPING,
            stats=InterpretationStats(),
        )

        def gen():
            yield "test"

        frame = ContinuationFrame(
            generator=gen(),
            source_info=None,
        )

        state.push_frame(frame)
        assert state.stack_depth == 1
        assert state.current_frame == frame
        assert state.stats.total_frames_created == 1
        assert state.stats.max_stack_depth == 1

        popped = state.pop_frame()
        assert popped == frame
        assert state.stack_depth == 0
        assert state.current_frame is None

    def test_max_stack_depth_tracking(self):
        state = InterpreterState(
            continuation_stack=[],
            current_item=None,
            context=ExecutionContext(),
            phase=InterpretationPhase.STEPPING,
            stats=InterpretationStats(),
        )

        def gen():
            yield "test"

        # Push 5 frames
        for _ in range(5):
            frame = ContinuationFrame(
                generator=gen(),
                source_info=None,
            )
            state.push_frame(frame)

        assert state.stats.max_stack_depth == 5

        # Pop 3 frames
        for _ in range(3):
            state.pop_frame()

        # Push 1 more - max should still be 5
        frame = ContinuationFrame(
            generator=gen(),
            source_info=None,
        )
        state.push_frame(frame)
        assert state.stats.max_stack_depth == 5

    def test_snapshot_creation(self):
        state = InterpreterState(
            continuation_stack=[],
            current_item=None,
            context=ExecutionContext(),
            phase=InterpretationPhase.STEPPING,
            stats=InterpretationStats(),
        )

        snapshot = state.snapshot()
        assert isinstance(snapshot, InterpreterStateSnapshot)
        assert snapshot.stack_depth == 0
        assert snapshot.phase == InterpretationPhase.STEPPING


class TestInterpretationStats:
    """Test InterpretationStats."""

    def test_default_values(self):
        stats = InterpretationStats()
        assert stats.total_steps == 0
        assert stats.total_effects_handled == 0
        assert stats.total_frames_created == 0
        assert stats.max_stack_depth == 0
        assert stats.total_exceptions_caught == 0
        assert stats.start_time_ns is None
        assert stats.end_time_ns is None

    def test_duration_calculation(self):
        stats = InterpretationStats(
            start_time_ns=1000000,
            end_time_ns=2000000,
        )
        assert stats.duration_ns == 1000000

    def test_duration_none_when_incomplete(self):
        stats = InterpretationStats(start_time_ns=1000000)
        assert stats.duration_ns is None

    def test_copy(self):
        stats = InterpretationStats(
            total_steps=10,
            total_effects_handled=5,
            total_frames_created=3,
            max_stack_depth=2,
        )
        copied = stats.copy()
        assert copied.total_steps == 10
        assert copied is not stats


# ============================================
# Unit Tests: Effect Stack Trace Types
# ============================================


class TestPythonLocation:
    """Test PythonLocation."""

    def test_format_without_code(self):
        loc = PythonLocation(
            filename="/path/to/file.py",
            line=42,
            function="test_func",
        )
        formatted = loc.format()
        assert "/path/to/file.py:42 in test_func" == formatted

    def test_format_with_code(self):
        loc = PythonLocation(
            filename="/path/to/file.py",
            line=42,
            function="test_func",
            code="result = yield some_effect",
        )
        formatted = loc.format()
        assert "/path/to/file.py:42 in test_func" in formatted
        assert "result = yield some_effect" in formatted


class TestEffectStackFrame:
    """Test EffectStackFrame."""

    def test_kleisli_call_frame(self):
        frame = EffectStackFrame(
            frame_type=EffectStackFrameType.KLEISLI_CALL,
            name="fetch_user",
            location=PythonLocation(
                filename="user.py", line=10, function="fetch_user"
            ),
            call_args=(123,),
            call_kwargs={"include_profile": True},
        )
        assert frame.frame_type == EffectStackFrameType.KLEISLI_CALL
        assert frame.name == "fetch_user"
        assert frame.call_args == (123,)

    def test_effect_yield_frame(self):
        frame = EffectStackFrame(
            frame_type=EffectStackFrameType.EFFECT_YIELD,
            name="Get",
            location=PythonLocation(
                filename="state.py", line=20, function="get_value"
            ),
        )
        assert frame.frame_type == EffectStackFrameType.EFFECT_YIELD


class TestEffectStackTraceRenderer:
    """Test EffectStackTraceRenderer."""

    def test_render_basic_trace(self):
        trace = EffectStackTrace(
            frames=(
                EffectStackFrame(
                    frame_type=EffectStackFrameType.KLEISLI_CALL,
                    name="outer_call",
                    location=PythonLocation("outer.py", 10, "outer_call"),
                ),
                EffectStackFrame(
                    frame_type=EffectStackFrameType.KLEISLI_CALL,
                    name="inner_call",
                    location=PythonLocation("inner.py", 20, "inner_call"),
                ),
                EffectStackFrame(
                    frame_type=EffectStackFrameType.EFFECT_YIELD,
                    name="Fail",
                    location=PythonLocation("inner.py", 25, "inner_call"),
                ),
            ),
            failed_effect=fail(ValueError("test error")),
            original_exception=ValueError("test error"),
            python_raise_location=PythonLocation("inner.py", 25, "inner_call"),
        )

        renderer = EffectStackTraceRenderer()
        output = renderer.render(trace)

        assert "EffectError" in output
        assert "ValueError" in output
        assert "outer_call" in output
        assert "inner_call" in output


# ============================================
# Integration Tests: Basic Functionality
# ============================================


class TestBasicFunctionality:
    """Test basic TrampolinedInterpreter functionality."""

    @pytest.mark.asyncio
    async def test_simple_state_program(self):
        @do
        def program() -> Generator[Effect, Any, int]:
            yield put("counter", 0)
            yield put("counter", 1)
            value = yield get("counter")
            return value

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(program())

        assert result.is_ok
        assert result.value == 1
        assert result.state["counter"] == 1

    @pytest.mark.asyncio
    async def test_simple_writer_program(self):
        @do
        def program() -> Generator[Effect, Any, str]:
            yield tell("Message 1")
            yield tell("Message 2")
            return "done"

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(program())

        assert result.is_ok
        assert result.value == "done"
        assert list(result.log) == ["Message 1", "Message 2"]

    @pytest.mark.asyncio
    async def test_simple_reader_program(self):
        @do
        def program() -> Generator[Effect, Any, str]:
            name = yield ask("name")
            return f"Hello, {name}!"

        interpreter = TrampolinedInterpreter()
        ctx = ExecutionContext(env={"name": "World"})
        result = await interpreter.run_async(program(), ctx)

        assert result.is_ok
        assert result.value == "Hello, World!"

    @pytest.mark.asyncio
    async def test_nested_programs(self):
        @do
        def inner_program() -> Generator[Effect, Any, int]:
            value = yield get("counter")
            yield put("counter", value + 1)
            return value + 1

        @do
        def outer_program() -> Generator[Effect, Any, int]:
            yield put("counter", 0)
            result1 = yield inner_program()
            result2 = yield inner_program()
            result3 = yield inner_program()
            return result1 + result2 + result3

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(outer_program())

        assert result.is_ok
        assert result.value == 1 + 2 + 3  # 6
        assert result.state["counter"] == 3

    @pytest.mark.asyncio
    async def test_error_handling_with_catch(self):
        @do
        def failing_program() -> Generator[Effect, Any, int]:
            yield tell("About to fail")
            yield fail(ValueError("test error"))
            return 0

        @do
        def recovery_program(error: BaseException) -> Generator[Effect, Any, int]:
            yield tell(f"Recovered from: {error}")
            return -1

        @do
        def main_program() -> Generator[Effect, Any, int]:
            result = yield catch(failing_program(), recovery_program)
            return result

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(main_program())

        assert result.is_ok
        assert result.value == -1
        assert "Recovered from" in str(result.log)

    @pytest.mark.asyncio
    async def test_error_propagation(self):
        @do
        def failing_program() -> Generator[Effect, Any, int]:
            yield fail(ValueError("test error"))
            return 0

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(failing_program())

        assert result.is_err

    @pytest.mark.asyncio
    async def test_finally_effect(self):
        @do
        def program_with_cleanup() -> Generator[Effect, Any, int]:
            yield put("step", 1)
            yield fail(ValueError("error"))
            return 0

        @do
        def cleanup() -> Generator[Effect, Any, None]:
            yield put("cleanup", True)
            return None

        @do
        def main_program() -> Generator[Effect, Any, int]:
            yield put("cleanup", False)
            yield finally_(program_with_cleanup(), cleanup())
            return 1

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(main_program())

        # Program should fail but cleanup should have run
        assert result.is_err
        assert result.state.get("cleanup") is True

    @pytest.mark.asyncio
    async def test_safe_effect(self):
        @do
        def maybe_failing() -> Generator[Effect, Any, int]:
            yield fail(ValueError("error"))
            return 42

        @do
        def main_program() -> Generator[Effect, Any, Any]:
            result = yield safe(maybe_failing())
            return result

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(main_program())

        assert result.is_ok
        from doeff._vendor import Err

        assert isinstance(result.value, Err)

    @pytest.mark.asyncio
    async def test_recover_effect(self):
        @do
        def failing_program() -> Generator[Effect, Any, int]:
            yield fail(ValueError("error"))
            return 0

        @do
        def recovery(exc: BaseException) -> Generator[Effect, Any, int]:
            return 42

        @do
        def main_program() -> Generator[Effect, Any, int]:
            result = yield recover(failing_program(), recovery)
            return result

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(main_program())

        assert result.is_ok
        assert result.value == 42


# ============================================
# Integration Tests: Async Effects
# ============================================


class TestAsyncEffects:
    """Test async effect handling."""

    @pytest.mark.asyncio
    async def test_await_effect(self):
        async def async_operation():
            await asyncio.sleep(0.001)
            return 42

        @do
        def program() -> Generator[Effect, Any, int]:
            value = yield await_(async_operation())
            return value

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(program())

        assert result.is_ok
        assert result.value == 42

    @pytest.mark.asyncio
    async def test_parallel_effect(self):
        async def task(n: int):
            await asyncio.sleep(0.001)
            return n * 2

        @do
        def program() -> Generator[Effect, Any, list]:
            results = yield parallel(*[task(i) for i in range(5)])
            return list(results)

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(program())

        assert result.is_ok
        assert result.value == [0, 2, 4, 6, 8]


# ============================================
# Integration Tests: Gather Effects
# ============================================


class TestGatherEffects:
    """Test gather effect handling."""

    @pytest.mark.asyncio
    async def test_gather_programs(self):
        @do
        def make_program(n: int) -> Generator[Effect, Any, int]:
            yield put(f"prog_{n}", n)
            return n * 2

        @do
        def main_program() -> Generator[Effect, Any, list]:
            programs = [make_program(i) for i in range(5)]
            results = yield gather(*programs)
            return list(results)

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(main_program())

        assert result.is_ok
        assert result.value == [0, 2, 4, 6, 8]


# ============================================
# Stack Safety Tests
# ============================================


class TestStackSafety:
    """Test stack safety with deep recursion."""

    @pytest.mark.asyncio
    async def test_deep_recursion_1000(self):
        """Test 1000 levels of recursion."""

        @do
        def recursive_program(n: int) -> Generator[Effect, Any, int]:
            if n <= 0:
                return 0
            yield put(f"level_{n}", n)
            sub_result = yield recursive_program(n - 1)
            return sub_result + 1

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(recursive_program(1000))

        assert result.is_ok
        assert result.value == 1000

    @pytest.mark.asyncio
    async def test_deep_recursion_5000(self):
        """Test 5000 levels of recursion."""

        @do
        def recursive_program(n: int) -> Generator[Effect, Any, int]:
            if n <= 0:
                return 0
            sub_result = yield recursive_program(n - 1)
            return sub_result + 1

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(recursive_program(5000))

        assert result.is_ok
        assert result.value == 5000

    @pytest.mark.asyncio
    async def test_deep_recursion_10000(self):
        """Test 10000 levels of recursion - validates stack safety."""

        @do
        def recursive_program(n: int) -> Generator[Effect, Any, int]:
            if n <= 0:
                return 0
            sub_result = yield recursive_program(n - 1)
            return sub_result + 1

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(recursive_program(10000))

        assert result.is_ok
        assert result.value == 10000

    @pytest.mark.asyncio
    async def test_deep_mixed_effects_chain(self):
        """Test deep chain with multiple effect types."""

        @do
        def mixed_program(n: int) -> Generator[Effect, Any, int]:
            if n <= 0:
                return 0

            # Multiple effects per level
            yield put(f"level_{n}", n)
            yield tell(f"At level {n}")
            config = yield ask("config")

            sub_result = yield mixed_program(n - 1)
            return sub_result + 1

        interpreter = TrampolinedInterpreter()
        ctx = ExecutionContext(env={"config": "test"})
        result = await interpreter.run_async(mixed_program(500), ctx)

        assert result.is_ok
        assert result.value == 500
        assert len(result.log) == 500

    @pytest.mark.asyncio
    async def test_stack_overflow_detection(self):
        """Test that stack overflow is detected and reported."""

        @do
        def infinite_recursion(n: int) -> Generator[Effect, Any, int]:
            sub_result = yield infinite_recursion(n + 1)
            return sub_result

        interpreter = TrampolinedInterpreter(max_stack_depth=100)
        result = await interpreter.run_async(infinite_recursion(0))

        assert result.is_err
        assert isinstance(result.result.error, ContinuationStackOverflowError)


# ============================================
# Error Handling Tests
# ============================================


class TestErrorHandling:
    """Test error handling and propagation."""

    @pytest.mark.asyncio
    async def test_exception_in_nested_program(self):
        """Test exception propagation through nested programs."""

        @do
        def inner() -> Generator[Effect, Any, int]:
            yield tell("inner start")
            yield fail(ValueError("inner error"))
            return 0

        @do
        def middle() -> Generator[Effect, Any, int]:
            yield tell("middle start")
            result = yield inner()
            yield tell("middle end")  # Should not reach
            return result

        @do
        def outer() -> Generator[Effect, Any, int]:
            yield tell("outer start")
            result = yield middle()
            yield tell("outer end")  # Should not reach
            return result

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(outer())

        assert result.is_err
        # Verify logs show where execution stopped
        assert "outer start" in str(result.log)
        assert "middle start" in str(result.log)
        assert "inner start" in str(result.log)
        assert "outer end" not in str(result.log)

    @pytest.mark.asyncio
    async def test_exception_caught_in_middle(self):
        """Test exception caught in the middle of the call stack."""

        @do
        def inner() -> Generator[Effect, Any, int]:
            yield fail(ValueError("error"))
            return 0

        @do
        def handler(exc: BaseException) -> Generator[Effect, Any, int]:
            yield tell(f"Caught: {exc}")
            return -1

        @do
        def middle() -> Generator[Effect, Any, int]:
            result = yield catch(inner(), handler)
            return result

        @do
        def outer() -> Generator[Effect, Any, int]:
            result = yield middle()
            yield tell("outer completed")
            return result

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(outer())

        assert result.is_ok
        assert result.value == -1
        assert "outer completed" in str(result.log)

    @pytest.mark.asyncio
    async def test_exception_raised_after_yield_is_propagated(self):
        """
        Regression test: exception raised after yield should propagate correctly.

        Previously, if a generator raised an exception after a yield, the frame
        would be marked FAILED but error propagation would try to throw into it,
        causing InvalidFrameStateError. This test ensures that case is handled.
        """

        @do
        def inner_raises_after_yield() -> Generator[Effect, Any, int]:
            yield tell("before effect")
            yield put("key", "value")  # After this yield...
            raise RuntimeError("error after yield")  # ...raise an exception

        @do
        def outer_catches() -> Generator[Effect, Any, int]:
            try:
                result = yield inner_raises_after_yield()
                return result
            except RuntimeError:
                yield tell("caught in outer")
                return -1

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(outer_catches())

        assert result.is_ok
        assert result.value == -1
        assert "caught in outer" in str(result.log)

    @pytest.mark.asyncio
    async def test_call_stack_preserved_on_error(self):
        """Test that call stack is captured at error time, not after unwinding."""
        from doeff.types import EffectFailure
        from doeff.kleisli import KleisliProgram

        # Use KleisliPrograms to ensure CallFrames are pushed
        @KleisliProgram
        @do
        def inner_fails() -> Generator[Effect, Any, int]:
            yield tell("inner")
            yield fail(ValueError("inner error"))
            return 0

        @KleisliProgram
        @do
        def middle() -> Generator[Effect, Any, int]:
            result = yield inner_fails()
            return result

        @KleisliProgram
        @do
        def outer() -> Generator[Effect, Any, int]:
            result = yield middle()
            return result

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(outer())

        assert result.is_err
        error = result.result.error
        assert isinstance(error, EffectFailure)
        # The call_stack_snapshot should have been captured before frames were popped
        # With KleisliPrograms, we should have call frames in the snapshot
        assert hasattr(error, 'call_stack_snapshot')
        assert error.call_stack_snapshot is not None
        # Should have at least outer -> middle -> inner_fails call stack
        assert len(error.call_stack_snapshot) >= 3, (
            f"Expected at least 3 call frames, got {len(error.call_stack_snapshot)}"
        )

    @pytest.mark.asyncio
    async def test_invalid_yield_raises_type_error(self):
        """Test that yielding non-Effect/Program raises TypeError."""

        @do
        def invalid_yield_program() -> Generator[Effect, Any, int]:
            yield tell("before")
            yield 42  # Invalid: yielding an integer
            return 0

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(invalid_yield_program())

        assert result.is_err
        error = result.result.error
        assert isinstance(error.cause, TypeError)
        assert "invalid type" in str(error.cause).lower()

    @pytest.mark.asyncio
    async def test_invalid_first_yield_raises_type_error(self):
        """Test that yielding invalid value as first yield raises TypeError."""

        @do
        def invalid_first_yield_program() -> Generator[Effect, Any, int]:
            yield 42  # Invalid: first yield is an integer
            return 0

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(invalid_first_yield_program())

        assert result.is_err
        error = result.result.error
        assert isinstance(error.cause, TypeError)
        assert "invalid type" in str(error.cause).lower()

    @pytest.mark.asyncio
    async def test_invalid_yield_in_exception_handler_raises_type_error(self):
        """Test that yielding invalid value in exception handler raises TypeError."""

        @do
        def program_with_invalid_catch() -> Generator[Effect, Any, int]:
            try:
                yield fail(ValueError("test error"))
            except ValueError:
                yield 99  # Invalid: yield integer in exception handler
            return 0

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(program_with_invalid_catch())

        assert result.is_err
        error = result.result.error
        assert isinstance(error.cause, TypeError)
        assert "invalid type" in str(error.cause).lower()


# ============================================
# IO Effect Tests
# ============================================


class TestIOEffects:
    """Test IO effect handling."""

    @pytest.mark.asyncio
    async def test_io_perform(self):
        side_effect = []

        @do
        def program() -> Generator[Effect, Any, int]:
            result = yield io(lambda: side_effect.append(1) or 42)
            return result

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(program())

        assert result.is_ok
        assert result.value == 42
        assert side_effect == [1]

    @pytest.mark.asyncio
    async def test_io_not_allowed(self):
        @do
        def program() -> Generator[Effect, Any, int]:
            result = yield io(lambda: 42)
            return result

        interpreter = TrampolinedInterpreter()
        ctx = ExecutionContext(io_allowed=False)
        result = await interpreter.run_async(program(), ctx)

        assert result.is_err


# ============================================
# Local Effect Tests
# ============================================


class TestLocalEffect:
    """Test local (Reader) effect."""

    @pytest.mark.asyncio
    async def test_local_modifies_environment_locally(self):
        @do
        def read_config() -> Generator[Effect, Any, str]:
            value = yield ask("config")
            return value

        @do
        def program() -> Generator[Effect, Any, tuple]:
            original = yield ask("config")
            local_result = yield local(
                {"config": "modified"},
                read_config(),
            )
            after = yield ask("config")
            return original, local_result, after

        interpreter = TrampolinedInterpreter()
        ctx = ExecutionContext(env={"config": "original"})
        result = await interpreter.run_async(program(), ctx)

        assert result.is_ok
        assert result.value == ("original", "modified", "original")


# ============================================
# Atomic State Tests
# ============================================


class TestAtomicState:
    """Test atomic state effects."""

    @pytest.mark.asyncio
    async def test_atomic_get_and_update(self):
        @do
        def program() -> Generator[Effect, Any, tuple]:
            yield atomic_update("counter", lambda x: (x or 0) + 1)
            yield atomic_update("counter", lambda x: x + 1)
            value = yield atomic_get("counter")
            return value

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(program())

        assert result.is_ok
        assert result.value == 2


# ============================================
# Listen Effect Tests
# ============================================


class TestListenEffect:
    """Test listen (Writer) effect."""

    @pytest.mark.asyncio
    async def test_listen_captures_log(self):
        @do
        def logged_program() -> Generator[Effect, Any, int]:
            yield tell("log 1")
            yield tell("log 2")
            return 42

        @do
        def program() -> Generator[Effect, Any, tuple]:
            yield tell("before")
            result = yield listen(logged_program())
            yield tell("after")
            return result

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(program())

        assert result.is_ok
        value, captured_log = result.value.value, result.value.log
        assert value == 42
        assert list(captured_log) == ["log 1", "log 2"]


# ============================================
# Performance/Benchmark Tests
# ============================================


class TestPerformance:
    """Performance tests for the interpreter."""

    @pytest.mark.asyncio
    async def test_many_effects_performance(self):
        """Test handling 10,000 effects."""

        @do
        def program() -> Generator[Effect, Any, int]:
            for i in range(10000):
                yield put(f"key_{i}", i)
            return 10000

        interpreter = TrampolinedInterpreter()

        start = time.perf_counter()
        result = await interpreter.run_async(program())
        elapsed = time.perf_counter() - start

        assert result.is_ok
        assert result.value == 10000
        # Should complete in reasonable time (< 5 seconds)
        assert elapsed < 5.0
        print(f"10K effects completed in {elapsed:.3f}s")

    @pytest.mark.asyncio
    async def test_deep_nesting_performance(self):
        """Test performance with deep nesting."""

        @do
        def recursive(n: int) -> Generator[Effect, Any, int]:
            if n <= 0:
                return 0
            sub = yield recursive(n - 1)
            return sub + 1

        interpreter = TrampolinedInterpreter()

        start = time.perf_counter()
        result = await interpreter.run_async(recursive(5000))
        elapsed = time.perf_counter() - start

        assert result.is_ok
        assert result.value == 5000
        # Should complete in reasonable time (< 10 seconds)
        assert elapsed < 10.0
        print(f"5K deep nesting completed in {elapsed:.3f}s")


# ============================================
# Reentrancy Tests
# ============================================


class TestReentrancy:
    """Test reentrancy handling - reentrant calls via handlers work correctly."""

    @pytest.mark.asyncio
    async def test_reentrant_calls_via_handlers_work(self):
        """Test that reentrant calls via catch/recover handlers work correctly.

        This tests that the interpreter can handle reentrant calls from
        effect handlers, which is required for catch/recover/etc. to work.
        """

        @do
        def inner() -> Generator[Effect, Any, int]:
            yield tell("inner executed")
            return 42

        @do
        def handler(e: BaseException) -> Generator[Effect, Any, int]:
            yield tell("handler executed")
            return -1

        @do
        def outer() -> Generator[Effect, Any, int]:
            # catch() will call interpreter.run_async() reentrantly
            result = yield catch(inner(), handler)
            yield tell(f"outer got: {result}")
            return result

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(outer())

        assert result.is_ok
        assert result.value == 42
        assert "inner executed" in str(result.log)

    @pytest.mark.asyncio
    async def test_nested_reentrant_calls_work(self):
        """Test deeply nested reentrant calls via handlers."""

        @do
        def level3() -> Generator[Effect, Any, int]:
            yield tell("level3")
            return 3

        @do
        def level2() -> Generator[Effect, Any, int]:
            yield tell("level2")
            result = yield catch(level3(), lambda e: level3())
            return result + 2

        @do
        def level1() -> Generator[Effect, Any, int]:
            yield tell("level1")
            result = yield catch(level2(), lambda e: level2())
            return result + 1

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(level1())

        assert result.is_ok
        assert result.value == 6  # 3 + 2 + 1


# ============================================
# Program-as-Data Tests
# ============================================


class TestProgramAsData:
    """Test that Programs returned from effects are data, not auto-executed."""

    @pytest.mark.asyncio
    async def test_program_returned_from_io_is_not_executed(self):
        """Test that a Program returned from IO effect is not auto-executed."""
        execution_count = [0]

        @do
        def inner_program() -> Generator[Effect, Any, int]:
            execution_count[0] += 1
            yield tell("inner executed")
            return 42

        @do
        def outer() -> Generator[Effect, Any, Any]:
            # IO returns a Program - it should NOT be executed
            result = yield io(lambda: inner_program())
            return result

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(outer())

        assert result.is_ok
        # Result should be the Program object, not 42
        from doeff.program import ProgramBase
        assert isinstance(result.value, ProgramBase)
        # Inner program should NOT have been executed
        assert execution_count[0] == 0

    @pytest.mark.asyncio
    async def test_program_stored_in_state_is_not_executed(self):
        """Test that a Program stored in state and retrieved is not auto-executed."""
        execution_count = [0]

        @do
        def inner_program() -> Generator[Effect, Any, int]:
            execution_count[0] += 1
            return 42

        @do
        def outer() -> Generator[Effect, Any, Any]:
            prog = inner_program()
            yield put("stored_program", prog)
            retrieved = yield get("stored_program")
            return retrieved

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(outer())

        assert result.is_ok
        from doeff.program import ProgramBase
        assert isinstance(result.value, ProgramBase)
        assert execution_count[0] == 0

    @pytest.mark.asyncio
    async def test_program_returned_from_catch_is_not_executed(self):
        """Test that a Program returned from catch handler is not auto-executed."""
        execution_count = [0]

        @do
        def inner_program() -> Generator[Effect, Any, int]:
            execution_count[0] += 1
            yield tell("inner executed")
            return 42

        @do
        def failing() -> Generator[Effect, Any, int]:
            yield fail(ValueError("error"))
            return 0

        @do
        def error_handler(e: BaseException) -> Generator[Effect, Any, Any]:
            # Return a Program from the handler - it should NOT be executed
            return inner_program()

        @do
        def outer() -> Generator[Effect, Any, Any]:
            result = yield catch(failing(), error_handler)
            return result

        interpreter = TrampolinedInterpreter()
        result = await interpreter.run_async(outer())

        assert result.is_ok
        from doeff.program import ProgramBase
        assert isinstance(result.value, ProgramBase)
        # Inner program should NOT have been executed
        assert execution_count[0] == 0


# ============================================
# Comparison with ProgramInterpreter
# ============================================


class TestProgramInterpreterParity:
    """Verify TrampolinedInterpreter matches ProgramInterpreter behavior."""

    @pytest.mark.asyncio
    async def test_same_result_basic(self):
        from doeff import ProgramInterpreter

        @do
        def program() -> Generator[Effect, Any, int]:
            yield put("x", 10)
            x = yield get("x")
            yield put("y", x * 2)
            y = yield get("y")
            return x + y

        old_interpreter = ProgramInterpreter()
        new_interpreter = TrampolinedInterpreter()

        old_result = await old_interpreter.run_async(program())
        new_result = await new_interpreter.run_async(program())

        assert old_result.value == new_result.value
        assert old_result.state == new_result.state

    @pytest.mark.asyncio
    async def test_same_result_with_error_handling(self):
        from doeff import ProgramInterpreter

        @do
        def failing() -> Generator[Effect, Any, int]:
            yield fail(ValueError("test"))
            return 0

        @do
        def handler(e: BaseException) -> Generator[Effect, Any, int]:
            return -1

        @do
        def program() -> Generator[Effect, Any, int]:
            result = yield catch(failing(), handler)
            return result

        old_interpreter = ProgramInterpreter()
        new_interpreter = TrampolinedInterpreter()

        old_result = await old_interpreter.run_async(program())
        new_result = await new_interpreter.run_async(program())

        assert old_result.value == new_result.value

    @pytest.mark.asyncio
    async def test_same_result_with_nested_programs(self):
        from doeff import ProgramInterpreter

        @do
        def inner(n: int) -> Generator[Effect, Any, int]:
            yield put(f"inner_{n}", n)
            return n * 2

        @do
        def outer() -> Generator[Effect, Any, int]:
            a = yield inner(1)
            b = yield inner(2)
            c = yield inner(3)
            return a + b + c

        old_interpreter = ProgramInterpreter()
        new_interpreter = TrampolinedInterpreter()

        old_result = await old_interpreter.run_async(outer())
        new_result = await new_interpreter.run_async(outer())

        assert old_result.value == new_result.value
        assert old_result.state == new_result.state


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
