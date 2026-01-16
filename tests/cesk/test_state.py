"""Unit tests for doeff.cesk.state module."""

from __future__ import annotations

import pytest
from doeff._vendor import FrozenDict
from doeff.cesk.state import (
    CESKState,
    Condition,
    Control,
    EffectControl,
    Error,
    FutureState,
    GatherCondition,
    ProgramControl,
    RaceCondition,
    TaskState,
    TaskStatus,
    TimeCondition,
    Value,
    WaitingOn,
)
from doeff.cesk.types import Environment, FutureId, Store, TaskId
from doeff.effects import PureEffect


class TestControlTypes:
    """Tests for Control type variants."""

    def test_value_control(self) -> None:
        """Value holds a computed value."""
        v = Value(42)
        assert v.v == 42

    def test_value_immutable(self) -> None:
        """Value is frozen dataclass."""
        v = Value(42)
        with pytest.raises(AttributeError):
            v.v = 100  # type: ignore[misc]

    def test_error_control(self) -> None:
        """Error holds an exception."""
        ex = ValueError("test error")
        e = Error(ex)
        assert e.ex is ex
        assert e.captured_traceback is None

    def test_error_with_traceback(self) -> None:
        """Error can store captured traceback."""
        ex = ValueError("test error")
        # Use None for traceback in test since CapturedTraceback is complex
        e = Error(ex, captured_traceback=None)
        assert e.ex is ex

    def test_effect_control(self) -> None:
        """EffectControl holds an effect."""
        effect = PureEffect(value=42)
        ec = EffectControl(effect)
        assert ec.effect is effect

    def test_program_control(self) -> None:
        """ProgramControl holds a program."""
        from doeff import do

        @do
        def simple() -> int:
            yield from PureEffect(42)
            return 42

        pc = ProgramControl(simple())
        assert pc.program is not None


class TestTaskStatus:
    """Tests for TaskStatus enum."""

    def test_all_statuses_exist(self) -> None:
        """All expected task statuses exist."""
        assert TaskStatus.RUNNING
        assert TaskStatus.BLOCKED
        assert TaskStatus.WAITING
        assert TaskStatus.DONE
        assert TaskStatus.FAILED
        assert TaskStatus.CANCELLED

    def test_statuses_are_distinct(self) -> None:
        """All statuses have distinct values."""
        statuses = [
            TaskStatus.RUNNING,
            TaskStatus.BLOCKED,
            TaskStatus.WAITING,
            TaskStatus.DONE,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ]
        assert len(set(statuses)) == len(statuses)


class TestConditions:
    """Tests for Condition types."""

    def test_waiting_on(self) -> None:
        """WaitingOn condition holds future ID."""
        future_id = FutureId(1)
        condition = WaitingOn(future_id)
        assert condition.future_id == future_id

    def test_gather_condition(self) -> None:
        """GatherCondition holds multiple future IDs."""
        f1, f2, f3 = FutureId(1), FutureId(2), FutureId(3)
        condition = GatherCondition(future_ids=(f1, f2, f3))
        assert condition.future_ids == (f1, f2, f3)
        assert condition.completed == frozenset()

    def test_gather_condition_with_completed(self) -> None:
        """GatherCondition tracks completed futures."""
        f1, f2, f3 = FutureId(1), FutureId(2), FutureId(3)
        condition = GatherCondition(
            future_ids=(f1, f2, f3), completed=frozenset({f1})
        )
        assert f1 in condition.completed
        assert f2 not in condition.completed

    def test_race_condition(self) -> None:
        """RaceCondition holds multiple future IDs."""
        f1, f2 = FutureId(1), FutureId(2)
        condition = RaceCondition(future_ids=(f1, f2))
        assert condition.future_ids == (f1, f2)

    def test_time_condition(self) -> None:
        """TimeCondition holds target time."""
        condition = TimeCondition(target_time=1000.0)
        assert condition.target_time == 1000.0


class TestTaskState:
    """Tests for TaskState."""

    def test_create_task_state(self) -> None:
        """TaskState can be created with all components."""
        task_id = TaskId(1)
        env: Environment = FrozenDict({"x": 10})
        task = TaskState(
            task_id=task_id,
            C=Value(42),
            E=env,
            K=[],
        )
        assert task.task_id == task_id
        assert isinstance(task.C, Value)
        assert task.E == env
        assert task.K == []
        assert task.status == TaskStatus.RUNNING
        assert task.condition is None
        assert task.future_id is None

    def test_with_control(self) -> None:
        """with_control returns new TaskState with updated control."""
        task_id = TaskId(1)
        env: Environment = FrozenDict()
        task = TaskState(task_id=task_id, C=Value(1), E=env, K=[])

        new_task = task.with_control(Value(2))

        assert task.C == Value(1)  # Original unchanged
        assert new_task.C == Value(2)
        assert new_task.task_id == task_id
        assert new_task.E is env

    def test_with_environment(self) -> None:
        """with_environment returns new TaskState with updated environment."""
        task_id = TaskId(1)
        env1: Environment = FrozenDict({"x": 1})
        env2: Environment = FrozenDict({"x": 2})
        task = TaskState(task_id=task_id, C=Value(1), E=env1, K=[])

        new_task = task.with_environment(env2)

        assert task.E == env1  # Original unchanged
        assert new_task.E == env2

    def test_with_kontinuation(self) -> None:
        """with_kontinuation returns new TaskState with updated K."""
        from doeff.cesk.frames import LocalFrame

        task_id = TaskId(1)
        env: Environment = FrozenDict()
        task = TaskState(task_id=task_id, C=Value(1), E=env, K=[])

        new_k = [LocalFrame(env)]
        new_task = task.with_kontinuation(new_k)

        assert task.K == []  # Original unchanged
        assert new_task.K == new_k

    def test_with_status(self) -> None:
        """with_status returns new TaskState with updated status."""
        task_id = TaskId(1)
        env: Environment = FrozenDict()
        task = TaskState(task_id=task_id, C=Value(1), E=env, K=[])

        condition = WaitingOn(FutureId(1))
        new_task = task.with_status(TaskStatus.WAITING, condition)

        assert task.status == TaskStatus.RUNNING  # Original unchanged
        assert new_task.status == TaskStatus.WAITING
        assert new_task.condition == condition


class TestFutureState:
    """Tests for FutureState."""

    def test_create_future_state(self) -> None:
        """FutureState can be created."""
        future_id = FutureId(1)
        producer = TaskId(0)
        future = FutureState(future_id=future_id, producer_task=producer)

        assert future.future_id == future_id
        assert future.producer_task == producer
        assert future.value is None
        assert future.error is None
        assert future.is_done is False
        assert future.waiters == frozenset()

    def test_with_value(self) -> None:
        """with_value returns FutureState with result."""
        future = FutureState(future_id=FutureId(1), producer_task=TaskId(0))

        completed = future.with_value(42)

        assert future.is_done is False  # Original unchanged
        assert completed.value == 42
        assert completed.error is None
        assert completed.is_done is True

    def test_with_error(self) -> None:
        """with_error returns FutureState with error."""
        future = FutureState(future_id=FutureId(1), producer_task=TaskId(0))
        ex = ValueError("test")

        failed = future.with_error(ex)

        assert future.is_done is False  # Original unchanged
        assert failed.value is None
        assert failed.error is ex
        assert failed.is_done is True

    def test_with_waiter(self) -> None:
        """with_waiter adds waiting task."""
        future = FutureState(future_id=FutureId(1), producer_task=TaskId(0))
        waiter = TaskId(1)

        waiting = future.with_waiter(waiter)

        assert future.waiters == frozenset()  # Original unchanged
        assert waiter in waiting.waiters


class TestCESKStateCreation:
    """Tests for CESKState creation."""

    def test_initial_creates_single_task(self) -> None:
        """initial() creates state with one root task."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def prog() -> int:
            yield from PureEffect(42)
            return 42

        state = CESKState.initial(prog())

        assert len(state.tasks) == 1
        root = TaskId(0)
        assert root in state.tasks
        assert state.tasks[root].status == TaskStatus.RUNNING
        assert isinstance(state.tasks[root].C, ProgramControl)

    def test_initial_with_env(self) -> None:
        """initial() accepts environment dict or FrozenDict."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def prog() -> int:
            yield from PureEffect(42)
            return 42

        # Dict env
        state1 = CESKState.initial(prog(), env={"x": 1})
        assert state1.E["x"] == 1

        # FrozenDict env
        state2 = CESKState.initial(prog(), env=FrozenDict({"y": 2}))
        assert state2.E["y"] == 2

    def test_initial_with_store(self) -> None:
        """initial() accepts initial store."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def prog() -> int:
            yield from PureEffect(42)
            return 42

        store: Store = {"counter": 0}
        state = CESKState.initial(prog(), store=store)
        assert state.S["counter"] == 0

    def test_from_single_task(self) -> None:
        """from_single_task creates state from CESK components."""
        env: Environment = FrozenDict({"x": 1})
        store: Store = {"y": 2}

        state = CESKState.from_single_task(
            C=Value(42),
            E=env,
            S=store,
            K=[],
        )

        assert state.C == Value(42)
        assert state.E == env
        assert state.S == store
        assert state.K == []


class TestCESKStateBackwardsCompatibility:
    """Tests for single-task backwards compatibility."""

    def test_c_property(self) -> None:
        """C property returns root task control."""
        env: Environment = FrozenDict()
        state = CESKState.from_single_task(C=Value(42), E=env, S={}, K=[])
        assert state.C == Value(42)

    def test_e_property(self) -> None:
        """E property returns root task environment."""
        env: Environment = FrozenDict({"x": 1})
        state = CESKState.from_single_task(C=Value(42), E=env, S={}, K=[])
        assert state.E == env

    def test_k_property(self) -> None:
        """K property returns root task kontinuation."""
        from doeff.cesk.frames import LocalFrame

        env: Environment = FrozenDict()
        k = [LocalFrame(env)]
        state = CESKState.from_single_task(C=Value(42), E=env, S={}, K=k)
        assert state.K == k

    def test_no_tasks_raises(self) -> None:
        """Accessing C/E/K on empty state raises ValueError."""
        state = CESKState(tasks={}, futures={}, S={})

        with pytest.raises(ValueError):
            _ = state.C
        with pytest.raises(ValueError):
            _ = state.E
        with pytest.raises(ValueError):
            _ = state.K


class TestCESKStateTaskManagement:
    """Tests for task and future management."""

    def test_allocate_task_id(self) -> None:
        """allocate_task_id returns new ID and updated state."""
        env: Environment = FrozenDict()
        state = CESKState.from_single_task(C=Value(42), E=env, S={}, K=[])

        new_state, task_id = state.allocate_task_id()

        assert task_id == TaskId(1)
        assert state.next_task_id == 1  # Original unchanged
        assert new_state.next_task_id == 2

    def test_allocate_future_id(self) -> None:
        """allocate_future_id returns new ID and updated state."""
        env: Environment = FrozenDict()
        state = CESKState.from_single_task(C=Value(42), E=env, S={}, K=[])

        new_state, future_id = state.allocate_future_id()

        assert future_id == FutureId(0)
        assert new_state.next_future_id == 1

    def test_add_task(self) -> None:
        """add_task adds task to state."""
        env: Environment = FrozenDict()
        state = CESKState.from_single_task(C=Value(42), E=env, S={}, K=[])

        new_task = TaskState(
            task_id=TaskId(1),
            C=Value(100),
            E=env,
            K=[],
        )
        new_state = state.add_task(new_task)

        assert len(state.tasks) == 1  # Original unchanged
        assert len(new_state.tasks) == 2
        assert TaskId(1) in new_state.tasks

    def test_update_task(self) -> None:
        """update_task updates existing task."""
        env: Environment = FrozenDict()
        state = CESKState.from_single_task(C=Value(42), E=env, S={}, K=[])

        root = TaskId(0)
        updated_task = state.tasks[root].with_control(Value(100))
        new_state = state.update_task(updated_task)

        assert state.tasks[root].C == Value(42)  # Original unchanged
        assert new_state.tasks[root].C == Value(100)

    def test_add_future(self) -> None:
        """add_future adds future to state."""
        env: Environment = FrozenDict()
        state = CESKState.from_single_task(C=Value(42), E=env, S={}, K=[])

        future = FutureState(future_id=FutureId(0), producer_task=TaskId(0))
        new_state = state.add_future(future)

        assert len(state.futures) == 0  # Original unchanged
        assert len(new_state.futures) == 1
        assert FutureId(0) in new_state.futures

    def test_update_future(self) -> None:
        """update_future updates existing future."""
        env: Environment = FrozenDict()
        state = CESKState.from_single_task(C=Value(42), E=env, S={}, K=[])
        future = FutureState(future_id=FutureId(0), producer_task=TaskId(0))
        state = state.add_future(future)

        completed = future.with_value(100)
        new_state = state.update_future(completed)

        assert state.futures[FutureId(0)].is_done is False  # Original unchanged
        assert new_state.futures[FutureId(0)].is_done is True

    def test_with_store(self) -> None:
        """with_store returns state with new store."""
        env: Environment = FrozenDict()
        state = CESKState.from_single_task(C=Value(42), E=env, S={"x": 1}, K=[])

        new_state = state.with_store({"x": 2})

        assert state.S["x"] == 1  # Original unchanged
        assert new_state.S["x"] == 2


class TestCESKStateQueries:
    """Tests for query methods."""

    def test_get_runnable_tasks(self) -> None:
        """get_runnable_tasks returns RUNNING tasks."""
        env: Environment = FrozenDict()
        state = CESKState.from_single_task(C=Value(42), E=env, S={}, K=[])

        # Add a waiting task
        waiting_task = TaskState(
            task_id=TaskId(1),
            C=Value(0),
            E=env,
            K=[],
            status=TaskStatus.WAITING,
        )
        state = state.add_task(waiting_task)

        runnable = state.get_runnable_tasks()
        assert TaskId(0) in runnable
        assert TaskId(1) not in runnable

    def test_get_waiting_tasks(self) -> None:
        """get_waiting_tasks returns WAITING tasks."""
        env: Environment = FrozenDict()
        state = CESKState.from_single_task(C=Value(42), E=env, S={}, K=[])

        waiting_task = TaskState(
            task_id=TaskId(1),
            C=Value(0),
            E=env,
            K=[],
            status=TaskStatus.WAITING,
            condition=WaitingOn(FutureId(0)),
        )
        state = state.add_task(waiting_task)

        waiting = state.get_waiting_tasks()
        assert TaskId(0) not in waiting
        assert TaskId(1) in waiting

    def test_is_all_done_false(self) -> None:
        """is_all_done returns False when tasks running."""
        env: Environment = FrozenDict()
        state = CESKState.from_single_task(C=Value(42), E=env, S={}, K=[])
        assert state.is_all_done() is False

    def test_is_all_done_true(self) -> None:
        """is_all_done returns True when all tasks terminal."""
        env: Environment = FrozenDict()
        state = CESKState.from_single_task(C=Value(42), E=env, S={}, K=[])

        # Mark root as done
        root = state.tasks[TaskId(0)]
        done_task = root.with_status(TaskStatus.DONE)
        state = state.update_task(done_task)

        assert state.is_all_done() is True
