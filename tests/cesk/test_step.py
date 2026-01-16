"""Tests for CESK step module."""

import pytest

from doeff._vendor import FrozenDict, Ok, Err
from doeff.program import Program
from doeff.cesk.types import TaskId, Store
from doeff.cesk.state import (
    CESKState,
    TaskState,
    ProgramControl,
    Value,
    Error,
    Ready,
    Blocked,
    Done as DoneStatus,
    FutureCondition,
)
from doeff.cesk.step import (
    step,
    step_task,
    step_cesk_task,
    InterpreterInvariantError,
)
from doeff.cesk.result import Done, Failed, Suspended
from doeff.cesk.types import FutureId


class TestStep:
    """Tests for the legacy step function."""

    def test_step_value_with_empty_k_returns_done(self) -> None:
        """Step Value with empty K returns Done."""
        task_state = TaskState(
            control=Value(42),
            env=FrozenDict(),
            kontinuation=[],
            status=Ready(),
        )
        main_id = TaskId.new()
        state = CESKState(
            tasks={main_id: task_state},
            store={},
            main_task=main_id,
        )

        result = step(state)

        assert isinstance(result, Done)
        assert result.value == 42

    def test_step_error_with_empty_k_returns_failed(self) -> None:
        """Step Error with empty K returns Failed."""
        error = ValueError("test error")
        task_state = TaskState(
            control=Error(error),
            env=FrozenDict(),
            kontinuation=[],
            status=Ready(),
        )
        main_id = TaskId.new()
        state = CESKState(
            tasks={main_id: task_state},
            store={},
            main_task=main_id,
        )

        result = step(state)

        assert isinstance(result, Failed)
        assert result.exception is error

    def test_step_program_control_starts_execution(self) -> None:
        """Step ProgramControl starts program execution."""
        program = Program.pure(42)
        state = CESKState.initial(program)

        result = step(state)

        # Either advances state or returns Suspended for the effect
        assert isinstance(result, (CESKState, Suspended))

    def test_step_legacy_interface_works(self) -> None:
        """CESKState legacy C, E, S, K interface works with step."""
        state = CESKState(C=Value(42), E=FrozenDict(), S={}, K=[])

        result = step(state)

        assert isinstance(result, Done)
        assert result.value == 42


class TestStepTask:
    """Tests for step_task function."""

    def test_step_task_requires_ready_status(self) -> None:
        """step_task raises error for non-Ready task."""
        program = Program.pure(42)
        task_state = TaskState.initial(program).with_status(
            Blocked(FutureCondition(FutureId.new()))
        )
        store: Store = {}

        with pytest.raises(InterpreterInvariantError, match="Cannot step task"):
            step_task(task_state, store)

    def test_step_task_value_with_empty_k(self) -> None:
        """step_task with Value and empty K returns Done."""
        task_state = TaskState(
            control=Value(42),
            env=FrozenDict(),
            kontinuation=[],
            status=Ready(),
        )
        store: Store = {}

        result = step_task(task_state, store)

        assert isinstance(result, Done)
        assert result.value == 42


class TestStepCeskTask:
    """Tests for step_cesk_task function."""

    def test_step_cesk_task_basic(self) -> None:
        """step_cesk_task steps a specific task."""
        program = Program.pure(42)
        cesk_state = CESKState.initial(program)

        new_state, result = step_cesk_task(cesk_state, cesk_state.main_task)

        # Should get a valid result
        assert isinstance(result, (CESKState, Done, Failed, Suspended))

    def test_step_cesk_task_value_done(self) -> None:
        """step_cesk_task marks task as Done when Value with empty K."""
        task_state = TaskState(
            control=Value(42),
            env=FrozenDict(),
            kontinuation=[],
            status=Ready(),
        )
        main_id = TaskId.new()
        cesk_state = CESKState(
            tasks={main_id: task_state},
            store={},
            main_task=main_id,
        )

        new_state, result = step_cesk_task(cesk_state, main_id)

        assert isinstance(result, Done)
        assert result.value == 42

        # Task should be marked as done in the new state
        updated_task = new_state.get_task(main_id)
        assert updated_task is not None
        assert isinstance(updated_task.status, DoneStatus)
        assert updated_task.status.result.is_ok()
        assert updated_task.status.result.unwrap() == 42

    def test_step_cesk_task_error_failed(self) -> None:
        """step_cesk_task marks task as Done with error when Error with empty K."""
        error = ValueError("test error")
        task_state = TaskState(
            control=Error(error),
            env=FrozenDict(),
            kontinuation=[],
            status=Ready(),
        )
        main_id = TaskId.new()
        cesk_state = CESKState(
            tasks={main_id: task_state},
            store={},
            main_task=main_id,
        )

        new_state, result = step_cesk_task(cesk_state, main_id)

        assert isinstance(result, Failed)
        assert result.exception is error

        # Task should be marked as done with error
        updated_task = new_state.get_task(main_id)
        assert updated_task is not None
        assert isinstance(updated_task.status, DoneStatus)
        assert updated_task.status.result.is_err()

    def test_step_cesk_task_invalid_task_id(self) -> None:
        """step_cesk_task raises error for invalid task ID."""
        program = Program.pure(42)
        cesk_state = CESKState.initial(program)
        fake_id = TaskId.new()

        with pytest.raises(ValueError, match="not found"):
            step_cesk_task(cesk_state, fake_id)

    def test_step_cesk_task_blocked_task(self) -> None:
        """step_cesk_task raises error for blocked task."""
        program = Program.pure(42)
        cesk_state = CESKState.initial(program)

        # Block the main task
        main_task = cesk_state.get_task(cesk_state.main_task)
        assert main_task is not None
        blocked_task = main_task.with_status(
            Blocked(FutureCondition(FutureId.new()))
        )
        cesk_state = cesk_state.with_task(cesk_state.main_task, blocked_task)

        with pytest.raises(InterpreterInvariantError, match="Cannot step task"):
            step_cesk_task(cesk_state, cesk_state.main_task)
