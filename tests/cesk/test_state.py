"""Tests for CESK state module."""

from datetime import datetime, timedelta

import pytest

from doeff._vendor import Err, FrozenDict, Ok
from doeff.cesk.state import (
    Blocked,
    CESKState,
    CreateFuture,
    # Requests
    CreateTask,
    Done,
    EffectControl,
    Error,
    FutureCondition,
    PerformIO,
    ProgramControl,
    # Task status
    Ready,
    Requesting,
    ResolveFuture,
    SpawnCondition,
    TaskCondition,
    # State classes
    TaskState,
    # Conditions
    TimeCondition,
    # Control states
    Value,
)
from doeff.cesk.types import FutureId, SpawnId, TaskId
from doeff.program import Program


class TestControlStates:
    """Tests for Control state types."""

    def test_value(self) -> None:
        """Value holds a computation result."""
        v = Value(42)
        assert v.v == 42

    def test_error(self) -> None:
        """Error holds an exception."""
        ex = ValueError("test error")
        e = Error(ex)
        assert e.ex is ex
        assert e.captured_traceback is None

    def test_error_with_traceback(self) -> None:
        """Error can hold a captured traceback."""
        ex = ValueError("test")
        # Just test the structure, not the actual traceback capture
        e = Error(ex, captured_traceback=None)
        assert e.captured_traceback is None

    def test_effect_control(self) -> None:
        """EffectControl holds an effect to handle."""
        from doeff.effects import PureEffect
        effect = PureEffect(value=42)
        ec = EffectControl(effect)
        assert ec.effect is effect

    def test_program_control(self) -> None:
        """ProgramControl holds a program to execute."""
        program = Program.pure(42)
        pc = ProgramControl(program)
        assert pc.program is program


class TestConditions:
    """Tests for Condition types."""

    def test_time_condition(self) -> None:
        """TimeCondition specifies a wake time."""
        wake_time = datetime.now() + timedelta(seconds=10)
        cond = TimeCondition(wake_time)
        assert cond.wake_time == wake_time

    def test_future_condition(self) -> None:
        """FutureCondition specifies a future to wait for."""
        future_id = FutureId.new()
        cond = FutureCondition(future_id)
        assert cond.future_id == future_id

    def test_task_condition(self) -> None:
        """TaskCondition specifies a task to wait for."""
        task_id = TaskId.new()
        cond = TaskCondition(task_id)
        assert cond.task_id == task_id

    def test_spawn_condition(self) -> None:
        """SpawnCondition specifies a spawn to wait for."""
        spawn_id = SpawnId.new()
        cond = SpawnCondition(spawn_id)
        assert cond.spawn_id == spawn_id


class TestRequests:
    """Tests for Request types."""

    def test_create_task(self) -> None:
        """CreateTask holds a program to spawn as a task."""
        program = Program.pure(42)
        req = CreateTask(program)
        assert req.program is program

    def test_create_future(self) -> None:
        """CreateFuture requests a new future."""
        req = CreateFuture()
        assert isinstance(req, CreateFuture)

    def test_resolve_future(self) -> None:
        """ResolveFuture resolves a future with a value."""
        future_id = FutureId.new()
        req = ResolveFuture(future_id, 42)
        assert req.future_id == future_id
        assert req.value == 42

    def test_perform_io(self) -> None:
        """PerformIO holds an IO action."""
        action = lambda: print("hello")
        req = PerformIO(action)
        assert req.action is action


class TestTaskStatus:
    """Tests for TaskStatus types."""

    def test_ready_default(self) -> None:
        """Ready with default resume value."""
        status = Ready()
        assert status.resume_value is None

    def test_ready_with_value(self) -> None:
        """Ready with specific resume value."""
        status = Ready(42)
        assert status.resume_value == 42

    def test_blocked(self) -> None:
        """Blocked with a condition."""
        future_id = FutureId.new()
        cond = FutureCondition(future_id)
        status = Blocked(cond)
        assert status.condition == cond

    def test_requesting(self) -> None:
        """Requesting with a request."""
        program = Program.pure(42)
        req = CreateTask(program)
        status = Requesting(req)
        assert status.request == req

    def test_done_ok(self) -> None:
        """Done.ok creates successful completion."""
        status = Done.ok(42)
        assert isinstance(status.result, Ok)
        assert status.result.value == 42

    def test_done_err(self) -> None:
        """Done.err creates failed completion."""
        error = ValueError("test")
        status = Done.err(error)
        assert isinstance(status.result, Err)
        assert status.result.error is error


class TestTaskState:
    """Tests for TaskState."""

    def test_initial(self) -> None:
        """TaskState.initial creates initial state for a program."""
        program = Program.pure(42)
        state = TaskState.initial(program)

        assert isinstance(state.control, ProgramControl)
        assert state.control.program is program
        assert isinstance(state.env, FrozenDict)
        assert len(state.env) == 0
        assert state.kontinuation == []
        assert isinstance(state.status, Ready)

    def test_initial_with_env(self) -> None:
        """TaskState.initial accepts environment."""
        program = Program.pure(42)
        env = {"key": "value"}
        state = TaskState.initial(program, env)

        assert state.env["key"] == "value"

    def test_with_control(self) -> None:
        """with_control updates control."""
        program = Program.pure(42)
        state = TaskState.initial(program)
        new_control = Value(42)

        new_state = state.with_control(new_control)

        assert new_state.control == new_control
        assert new_state.env is state.env
        assert new_state.kontinuation is state.kontinuation
        assert new_state.status is state.status

    def test_with_env(self) -> None:
        """with_env updates environment."""
        program = Program.pure(42)
        state = TaskState.initial(program)
        new_env: FrozenDict = FrozenDict({"new": "env"})

        new_state = state.with_env(new_env)

        assert new_state.env == new_env
        assert new_state.control is state.control

    def test_with_status(self) -> None:
        """with_status updates status."""
        program = Program.pure(42)
        state = TaskState.initial(program)
        new_status = Done.ok(42)

        new_state = state.with_status(new_status)

        assert new_state.status == new_status

    def test_resume_with(self) -> None:
        """resume_with sets control to Value and status to Ready."""
        program = Program.pure(42)
        state = TaskState.initial(program)

        resumed = state.resume_with(100)

        assert isinstance(resumed.control, Value)
        assert resumed.control.v == 100
        assert isinstance(resumed.status, Ready)
        assert resumed.status.resume_value == 100

    def test_fail_with(self) -> None:
        """fail_with sets control to Error."""
        program = Program.pure(42)
        state = TaskState.initial(program)
        error = ValueError("test error")

        failed = state.fail_with(error)

        assert isinstance(failed.control, Error)
        assert failed.control.ex is error
        assert isinstance(failed.status, Ready)


class TestCESKState:
    """Tests for CESKState."""

    def test_initial(self) -> None:
        """CESKState.initial creates state with single main task."""
        program = Program.pure(42)
        state = CESKState.initial(program)

        assert len(state.tasks) == 1
        assert state.main_task in state.tasks
        assert state.store == {}
        assert state.futures == {}
        assert state.spawn_results == {}

    def test_initial_with_env_and_store(self) -> None:
        """CESKState.initial accepts env and store."""
        program = Program.pure(42)
        env = {"key": "value"}
        store = {"state_key": "state_value"}

        state = CESKState.initial(program, env, store)

        assert state.E["key"] == "value"
        assert state.S["state_key"] == "state_value"

    def test_legacy_interface(self) -> None:
        """CESKState provides C, E, S, K properties for backward compatibility."""
        program = Program.pure(42)
        state = CESKState.initial(program)

        # These should access main task's components
        assert isinstance(state.C, ProgramControl)
        assert isinstance(state.E, FrozenDict)
        assert isinstance(state.S, dict)
        assert isinstance(state.K, list)

    def test_get_task(self) -> None:
        """get_task retrieves task state."""
        program = Program.pure(42)
        state = CESKState.initial(program)

        task = state.get_task(state.main_task)
        assert task is not None
        assert isinstance(task.control, ProgramControl)

        # Non-existent task returns None
        fake_id = TaskId.new()
        assert state.get_task(fake_id) is None

    def test_with_task(self) -> None:
        """with_task updates a task's state."""
        program = Program.pure(42)
        state = CESKState.initial(program)

        task = state.get_task(state.main_task)
        assert task is not None
        updated_task = task.with_control(Value(100))

        new_state = state.with_task(state.main_task, updated_task)

        assert new_state.get_task(state.main_task) is updated_task
        # Original unchanged
        assert state.get_task(state.main_task) is task

    def test_add_task(self) -> None:
        """add_task adds a new task."""
        program = Program.pure(42)
        state = CESKState.initial(program)

        new_task_id = TaskId.new()
        new_task = TaskState.initial(Program.pure(100))

        new_state = state.add_task(new_task_id, new_task)

        assert len(new_state.tasks) == 2
        assert new_state.get_task(new_task_id) is new_task

    def test_add_task_duplicate_raises(self) -> None:
        """add_task raises for duplicate task ID."""
        program = Program.pure(42)
        state = CESKState.initial(program)

        with pytest.raises(ValueError, match="already exists"):
            state.add_task(state.main_task, TaskState.initial(Program.pure(100)))

    def test_remove_task(self) -> None:
        """remove_task removes a non-main task."""
        program = Program.pure(42)
        state = CESKState.initial(program)

        new_task_id = TaskId.new()
        new_task = TaskState.initial(Program.pure(100))
        state = state.add_task(new_task_id, new_task)

        new_state = state.remove_task(new_task_id)

        assert len(new_state.tasks) == 1
        assert new_state.get_task(new_task_id) is None

    def test_remove_main_task_raises(self) -> None:
        """remove_task raises for main task."""
        program = Program.pure(42)
        state = CESKState.initial(program)

        with pytest.raises(ValueError, match="Cannot remove main task"):
            state.remove_task(state.main_task)

    def test_futures(self) -> None:
        """Future operations work correctly."""
        program = Program.pure(42)
        state = CESKState.initial(program)

        future_id = FutureId.new()
        assert not state.is_future_resolved(future_id)
        assert state.get_future(future_id) is None

        new_state = state.with_future(future_id, "result")

        assert new_state.is_future_resolved(future_id)
        assert new_state.get_future(future_id) == "result"

    def test_spawn_results(self) -> None:
        """Spawn result operations work correctly."""
        program = Program.pure(42)
        state = CESKState.initial(program)

        spawn_id = SpawnId.new()
        new_state = state.with_spawn_result(spawn_id, "spawn_result")

        assert new_state.spawn_results[spawn_id] == "spawn_result"

    def test_get_ready_tasks(self) -> None:
        """get_ready_tasks returns tasks with Ready status."""
        program = Program.pure(42)
        state = CESKState.initial(program)

        ready_tasks = state.get_ready_tasks()
        assert len(ready_tasks) == 1
        assert state.main_task in ready_tasks

        # Add a blocked task
        blocked_task_id = TaskId.new()
        blocked_task = TaskState.initial(Program.pure(100)).with_status(
            Blocked(FutureCondition(FutureId.new()))
        )
        state = state.add_task(blocked_task_id, blocked_task)

        ready_tasks = state.get_ready_tasks()
        assert len(ready_tasks) == 1
        assert state.main_task in ready_tasks
        assert blocked_task_id not in ready_tasks

    def test_is_main_task_done(self) -> None:
        """is_main_task_done checks main task completion."""
        program = Program.pure(42)
        state = CESKState.initial(program)

        assert not state.is_main_task_done()

        # Mark main task as done
        main_task = state.get_task(state.main_task)
        assert main_task is not None
        done_task = main_task.with_status(Done.ok(42))
        state = state.with_task(state.main_task, done_task)

        assert state.is_main_task_done()

    def test_get_main_result(self) -> None:
        """get_main_result returns result when main task is done."""
        program = Program.pure(42)
        state = CESKState.initial(program)

        assert state.get_main_result() is None

        # Mark main task as done
        main_task = state.get_task(state.main_task)
        assert main_task is not None
        done_task = main_task.with_status(Done.ok(42))
        state = state.with_task(state.main_task, done_task)

        result = state.get_main_result()
        assert isinstance(result, Ok)
        assert result.value == 42
