"""Unit tests for the CESK step function."""

from __future__ import annotations

import pytest
from doeff._vendor import FrozenDict, Ok, Err
from doeff.cesk.types import Environment, FutureId, Store, TaskId
from doeff.cesk.state import (
    CESKState,
    EffectControl,
    Error,
    FutureState,
    ProgramControl,
    TaskState,
    TaskStatus,
    Value,
    WaitingOn,
)
from doeff.cesk.frames import LocalFrame, ReturnFrame, SafeFrame
from doeff.cesk.actions import Resume, ResumeError, ResumeWithStore, RunProgram
from doeff.cesk.events import (
    FutureResolved,
    TaskDone,
    TaskFailed,
    TaskReady,
)
from doeff.cesk.step import (
    InterpreterInvariantError,
    StepOutput,
    UnhandledEffectError,
    resume_task,
    resume_task_error,
    resume_task_with_store,
    step,
)
from doeff.cesk.handlers import HandlerContext, HandlerRegistry, HandlerResult
from doeff.effects import PureEffect, AskEffect, StateGetEffect


# ============================================================================
# Test Fixtures
# ============================================================================


def make_initial_state(
    control: Value | Error | EffectControl | ProgramControl,
    env: dict | None = None,
    store: dict | None = None,
    kontinuation: list | None = None,
) -> CESKState:
    """Create a test CESKState with a single root task."""
    root_task = TaskState(
        task_id=TaskId(0),
        C=control,
        E=FrozenDict(env or {}),
        K=kontinuation or [],
        status=TaskStatus.RUNNING,
    )
    return CESKState(
        tasks={TaskId(0): root_task},
        futures={},
        S=store or {},
        next_task_id=1,
        next_future_id=0,
    )


def simple_handlers() -> HandlerRegistry:
    """Create a minimal handler registry for testing."""
    from doeff.effects import PureEffect, AskEffect, StateGetEffect

    def handle_pure(effect: PureEffect, ctx: HandlerContext) -> HandlerResult:
        return HandlerResult.resume(effect.value)

    def handle_ask(effect: AskEffect, ctx: HandlerContext) -> HandlerResult:
        value = ctx.env.get(effect.key)
        return HandlerResult.resume(value)

    def handle_get(effect: StateGetEffect, ctx: HandlerContext) -> HandlerResult:
        value = ctx.store.get(effect.key)
        return HandlerResult.resume(value)

    return {
        PureEffect: handle_pure,
        AskEffect: handle_ask,
        StateGetEffect: handle_get,
    }


# ============================================================================
# Test StepOutput
# ============================================================================


class TestStepOutput:
    """Tests for StepOutput dataclass."""

    def test_step_output_creation(self) -> None:
        """StepOutput holds state and events."""
        state = make_initial_state(Value(42))
        output = StepOutput(state=state, events=())
        assert output.state is state
        assert output.events == ()

    def test_step_output_with_events(self) -> None:
        """StepOutput can hold multiple events."""
        state = make_initial_state(Value(42))
        events = (TaskDone(TaskId(0), 42, {}), TaskReady(TaskId(1)))
        output = StepOutput(state=state, events=events)
        assert len(output.events) == 2


# ============================================================================
# Test Terminal States
# ============================================================================


class TestTerminalStates:
    """Tests for terminal state handling (Value/Error with empty K)."""

    def test_value_with_empty_k_emits_task_done(self) -> None:
        """Value control with empty K emits TaskDone event."""
        state = make_initial_state(Value(42))
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        assert len(output.events) == 1
        assert isinstance(output.events[0], TaskDone)
        assert output.events[0].task_id == TaskId(0)
        assert output.events[0].value == 42

    def test_value_with_empty_k_sets_task_done_status(self) -> None:
        """Value control with empty K sets task status to DONE."""
        state = make_initial_state(Value("result"))
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        task = output.state.tasks[TaskId(0)]
        assert task.status == TaskStatus.DONE

    def test_error_with_empty_k_emits_task_failed(self) -> None:
        """Error control with empty K emits TaskFailed event."""
        error = ValueError("test error")
        state = make_initial_state(Error(error))
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        assert len(output.events) == 1
        assert isinstance(output.events[0], TaskFailed)
        assert output.events[0].task_id == TaskId(0)
        assert output.events[0].error is error

    def test_error_with_empty_k_sets_task_failed_status(self) -> None:
        """Error control with empty K sets task status to FAILED."""
        error = RuntimeError("failed")
        state = make_initial_state(Error(error))
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        task = output.state.tasks[TaskId(0)]
        assert task.status == TaskStatus.FAILED

    def test_completed_task_resolves_associated_future(self) -> None:
        """When task with future completes, future is resolved."""
        future_id = FutureId(0)
        root_task = TaskState(
            task_id=TaskId(0),
            C=Value(100),
            E=FrozenDict(),
            K=[],
            status=TaskStatus.RUNNING,
            future_id=future_id,
        )
        future = FutureState(
            future_id=future_id,
            producer_task=TaskId(0),
        )
        state = CESKState(
            tasks={TaskId(0): root_task},
            futures={future_id: future},
            S={},
            next_task_id=1,
            next_future_id=1,
        )
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        # Check future resolved event
        resolved_events = [e for e in output.events if isinstance(e, FutureResolved)]
        assert len(resolved_events) == 1
        assert resolved_events[0].future_id == future_id
        assert resolved_events[0].value == 100

        # Check future state
        updated_future = output.state.futures[future_id]
        assert updated_future.is_done
        assert updated_future.value == 100

    def test_completed_task_wakes_waiters(self) -> None:
        """When task completes, waiters are notified via TaskReady events."""
        future_id = FutureId(0)
        waiter_id = TaskId(1)
        root_task = TaskState(
            task_id=TaskId(0),
            C=Value("done"),
            E=FrozenDict(),
            K=[],
            status=TaskStatus.RUNNING,
            future_id=future_id,
        )
        waiter_task = TaskState(
            task_id=waiter_id,
            C=Value(None),  # Waiting state
            E=FrozenDict(),
            K=[],
            status=TaskStatus.WAITING,
            condition=WaitingOn(future_id),
        )
        future = FutureState(
            future_id=future_id,
            producer_task=TaskId(0),
            waiters=frozenset({waiter_id}),
        )
        state = CESKState(
            tasks={TaskId(0): root_task, waiter_id: waiter_task},
            futures={future_id: future},
            S={},
            next_task_id=2,
            next_future_id=1,
        )
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        # Check TaskReady event for waiter
        ready_events = [e for e in output.events if isinstance(e, TaskReady)]
        assert len(ready_events) == 1
        assert ready_events[0].task_id == waiter_id


# ============================================================================
# Test Effect Handling
# ============================================================================


class TestEffectHandling:
    """Tests for effect handling through handlers."""

    def test_pure_effect_resumes_with_value(self) -> None:
        """PureEffect handler resumes with the effect's value."""
        effect = PureEffect(value=99)
        state = make_initial_state(EffectControl(effect))
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        task = output.state.tasks[TaskId(0)]
        assert isinstance(task.C, Value)
        assert task.C.v == 99

    def test_ask_effect_returns_env_value(self) -> None:
        """AskEffect handler returns value from environment."""
        effect = AskEffect(key="x")
        state = make_initial_state(EffectControl(effect), env={"x": 42})
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        task = output.state.tasks[TaskId(0)]
        assert isinstance(task.C, Value)
        assert task.C.v == 42

    def test_ask_effect_returns_none_for_missing_key(self) -> None:
        """AskEffect handler returns None for missing key."""
        effect = AskEffect(key="missing")
        state = make_initial_state(EffectControl(effect), env={})
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        task = output.state.tasks[TaskId(0)]
        assert isinstance(task.C, Value)
        assert task.C.v is None

    def test_state_get_effect_returns_store_value(self) -> None:
        """StateGetEffect handler returns value from store."""
        effect = StateGetEffect(key="counter")
        state = make_initial_state(EffectControl(effect), store={"counter": 10})
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        task = output.state.tasks[TaskId(0)]
        assert isinstance(task.C, Value)
        assert task.C.v == 10

    def test_unhandled_effect_causes_error(self) -> None:
        """Unhandled effect causes UnhandledEffectError."""
        # Create an effect with no handler
        effect = PureEffect(value=1)
        state = make_initial_state(EffectControl(effect))
        handlers: HandlerRegistry = {}  # Empty handlers

        output = step(state, TaskId(0), handlers)

        task = output.state.tasks[TaskId(0)]
        assert isinstance(task.C, Error)
        assert isinstance(task.C.ex, UnhandledEffectError)

    def test_handler_exception_becomes_error_control(self) -> None:
        """Exception in handler becomes Error control."""

        def bad_handler(effect, ctx):
            raise RuntimeError("handler crashed")

        effect = PureEffect(value=1)
        state = make_initial_state(EffectControl(effect))
        handlers = {PureEffect: bad_handler}

        output = step(state, TaskId(0), handlers)

        task = output.state.tasks[TaskId(0)]
        assert isinstance(task.C, Error)
        assert isinstance(task.C.ex, RuntimeError)
        assert "handler crashed" in str(task.C.ex)


# ============================================================================
# Test Frame Processing
# ============================================================================


class TestFrameProcessing:
    """Tests for frame processing with values and errors."""

    def test_value_through_local_frame_restores_env(self) -> None:
        """Value through LocalFrame restores original environment."""
        original_env = FrozenDict({"x": 1})
        local_frame = LocalFrame(restore_env=original_env)
        root_task = TaskState(
            task_id=TaskId(0),
            C=Value(42),
            E=FrozenDict({"x": 2}),  # Modified env
            K=[local_frame],
            status=TaskStatus.RUNNING,
        )
        state = CESKState(
            tasks={TaskId(0): root_task},
            futures={},
            S={},
            next_task_id=1,
            next_future_id=0,
        )
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        task = output.state.tasks[TaskId(0)]
        assert isinstance(task.C, Value)
        assert task.C.v == 42
        assert task.E == original_env
        assert task.K == []

    def test_error_through_local_frame_propagates(self) -> None:
        """Error through LocalFrame propagates and restores env."""
        original_env = FrozenDict({"x": 1})
        local_frame = LocalFrame(restore_env=original_env)
        error = ValueError("test")
        root_task = TaskState(
            task_id=TaskId(0),
            C=Error(error),
            E=FrozenDict({"x": 2}),
            K=[local_frame],
            status=TaskStatus.RUNNING,
        )
        state = CESKState(
            tasks={TaskId(0): root_task},
            futures={},
            S={},
            next_task_id=1,
            next_future_id=0,
        )
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        task = output.state.tasks[TaskId(0)]
        assert isinstance(task.C, Error)
        assert task.C.ex is error
        assert task.K == []

    def test_value_through_safe_frame_wraps_in_ok(self) -> None:
        """Value through SafeFrame is wrapped in Ok."""
        saved_env = FrozenDict()
        safe_frame = SafeFrame(saved_env=saved_env)
        root_task = TaskState(
            task_id=TaskId(0),
            C=Value(42),
            E=FrozenDict(),
            K=[safe_frame],
            status=TaskStatus.RUNNING,
        )
        state = CESKState(
            tasks={TaskId(0): root_task},
            futures={},
            S={},
            next_task_id=1,
            next_future_id=0,
        )
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        task = output.state.tasks[TaskId(0)]
        assert isinstance(task.C, Value)
        result = task.C.v
        assert isinstance(result, Ok)
        assert result.ok() == 42

    def test_error_through_safe_frame_wraps_in_err(self) -> None:
        """Error through SafeFrame is wrapped in Err."""
        saved_env = FrozenDict()
        safe_frame = SafeFrame(saved_env=saved_env)
        error = ValueError("test error")
        root_task = TaskState(
            task_id=TaskId(0),
            C=Error(error),
            E=FrozenDict(),
            K=[safe_frame],
            status=TaskStatus.RUNNING,
        )
        state = CESKState(
            tasks={TaskId(0): root_task},
            futures={},
            S={},
            next_task_id=1,
            next_future_id=0,
        )
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        task = output.state.tasks[TaskId(0)]
        assert isinstance(task.C, Value)
        result = task.C.v
        assert isinstance(result, Err)
        assert result.error == error


# ============================================================================
# Test Resume Helpers
# ============================================================================


class TestResumeHelpers:
    """Tests for resume_task, resume_task_error, resume_task_with_store."""

    def test_resume_task_sets_value_control(self) -> None:
        """resume_task sets Value control with given value."""
        root_task = TaskState(
            task_id=TaskId(0),
            C=Value(None),  # Placeholder
            E=FrozenDict(),
            K=[],
            status=TaskStatus.BLOCKED,
        )
        state = CESKState(
            tasks={TaskId(0): root_task},
            futures={},
            S={},
            next_task_id=1,
            next_future_id=0,
        )

        new_state = resume_task(state, TaskId(0), "io_result")

        task = new_state.tasks[TaskId(0)]
        assert isinstance(task.C, Value)
        assert task.C.v == "io_result"
        assert task.status == TaskStatus.RUNNING

    def test_resume_task_error_sets_error_control(self) -> None:
        """resume_task_error sets Error control with given error."""
        root_task = TaskState(
            task_id=TaskId(0),
            C=Value(None),
            E=FrozenDict(),
            K=[],
            status=TaskStatus.BLOCKED,
        )
        state = CESKState(
            tasks={TaskId(0): root_task},
            futures={},
            S={},
            next_task_id=1,
            next_future_id=0,
        )
        error = IOError("network failed")

        new_state = resume_task_error(state, TaskId(0), error)

        task = new_state.tasks[TaskId(0)]
        assert isinstance(task.C, Error)
        assert task.C.ex is error
        assert task.status == TaskStatus.RUNNING

    def test_resume_task_with_store_updates_store(self) -> None:
        """resume_task_with_store updates both value and store."""
        root_task = TaskState(
            task_id=TaskId(0),
            C=Value(None),
            E=FrozenDict(),
            K=[],
            status=TaskStatus.BLOCKED,
        )
        state = CESKState(
            tasks={TaskId(0): root_task},
            futures={},
            S={"old": "data"},
            next_task_id=1,
            next_future_id=0,
        )
        new_store = {"new": "store", "counter": 5}

        new_state = resume_task_with_store(state, TaskId(0), "result", new_store)

        task = new_state.tasks[TaskId(0)]
        assert isinstance(task.C, Value)
        assert task.C.v == "result"
        assert new_state.S == new_store

    def test_resume_task_raises_for_missing_task(self) -> None:
        """resume_task raises KeyError for missing task."""
        state = CESKState(
            tasks={},
            futures={},
            S={},
            next_task_id=1,
            next_future_id=0,
        )

        with pytest.raises(KeyError):
            resume_task(state, TaskId(99), "value")


# ============================================================================
# Test Error Cases
# ============================================================================


class TestErrorCases:
    """Tests for error handling and edge cases."""

    def test_step_raises_for_missing_task(self) -> None:
        """step raises KeyError for missing task ID."""
        state = CESKState(
            tasks={},
            futures={},
            S={},
            next_task_id=1,
            next_future_id=0,
        )
        handlers = simple_handlers()

        with pytest.raises(KeyError):
            step(state, TaskId(99), handlers)

    def test_step_with_captured_traceback_preserved(self) -> None:
        """Error with captured traceback is preserved."""
        error = ValueError("test")
        captured = object()  # Mock traceback
        root_task = TaskState(
            task_id=TaskId(0),
            C=Error(error, captured_traceback=captured),
            E=FrozenDict(),
            K=[],
            status=TaskStatus.RUNNING,
        )
        state = CESKState(
            tasks={TaskId(0): root_task},
            futures={},
            S={},
            next_task_id=1,
            next_future_id=0,
        )
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        event = output.events[0]
        assert isinstance(event, TaskFailed)
        assert event.captured_traceback is captured


# ============================================================================
# Test Program Execution
# ============================================================================


class TestProgramExecution:
    """Tests for ProgramControl handling."""

    def test_program_control_starts_generator_with_raw_yield(self) -> None:
        """ProgramControl starts a generator that directly yields an effect."""
        from doeff.effects import PureEffect

        # Use raw generator that yields effect directly
        def simple_prog():
            result = yield PureEffect(value=42)
            return result

        state = make_initial_state(ProgramControl(simple_prog()))
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        task = output.state.tasks[TaskId(0)]
        # After starting, should have EffectControl with PureEffect
        assert isinstance(task.C, EffectControl)
        assert isinstance(task.C.effect, PureEffect)
        # Should have ReturnFrame on stack
        assert len(task.K) == 1
        assert isinstance(task.K[0], ReturnFrame)

    def test_program_with_do_decorator(self) -> None:
        """@do decorated program can be stepped."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def simple_prog() -> int:
            result = yield from PureEffect(value=42)
            return result

        state = make_initial_state(ProgramControl(simple_prog()))
        handlers = simple_handlers()

        # First step starts the generator
        output = step(state, TaskId(0), handlers)
        task = output.state.tasks[TaskId(0)]

        # The @do program wraps effects in GeneratorProgram, so we get ProgramControl
        # Keep stepping until we reach EffectControl or Value
        while isinstance(task.C, ProgramControl):
            output = step(output.state, TaskId(0), handlers)
            task = output.state.tasks[TaskId(0)]

        # Should eventually reach the PureEffect
        assert isinstance(task.C, EffectControl) or isinstance(task.C, Value)
        if isinstance(task.C, EffectControl):
            assert isinstance(task.C.effect, PureEffect)

    def test_program_that_returns_immediately(self) -> None:
        """Program that returns without yielding completes."""
        # Use a raw generator instead of @do for immediate return
        def instant_prog():
            return 99
            yield  # Make it a generator

        state = make_initial_state(ProgramControl(instant_prog()))
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        task = output.state.tasks[TaskId(0)]
        assert isinstance(task.C, Value)
        assert task.C.v == 99

    def test_program_that_raises_becomes_error(self) -> None:
        """Program that raises becomes Error control."""
        # Use a raw generator for exception raising
        def failing_prog():
            raise RuntimeError("program failed")
            yield  # Make it a generator

        state = make_initial_state(ProgramControl(failing_prog()))
        handlers = simple_handlers()

        output = step(state, TaskId(0), handlers)

        task = output.state.tasks[TaskId(0)]
        assert isinstance(task.C, Error)
        assert isinstance(task.C.ex, RuntimeError)
