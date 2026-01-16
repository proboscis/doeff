"""Unit tests for doeff.cesk.actions module."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from doeff._vendor import FrozenDict
from doeff.cesk.actions import (
    Action,
    AwaitExternal,
    CancelTasks,
    CreateTask,
    CreateTasks,
    IOAction,
    ModifyStore,
    PerformIO,
    RaceOnFutures,
    Resume,
    ResumeError,
    ResumeWithStore,
    RunProgram,
    SyncAction,
    TaskAction,
    WaitAction,
    WaitForDuration,
    WaitOnFuture,
    WaitOnFutures,
    WaitUntilTime,
)
from doeff.cesk.types import Environment, FutureId, TaskId


class TestSyncActions:
    """Tests for synchronous action types."""

    def test_resume(self) -> None:
        """Resume holds a value."""
        action = Resume(value=42)
        assert action.value == 42

    def test_resume_error(self) -> None:
        """ResumeError holds an exception."""
        ex = ValueError("test error")
        action = ResumeError(error=ex)
        assert action.error is ex

    def test_run_program(self) -> None:
        """RunProgram holds a program."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def prog() -> int:
            yield from PureEffect(42)
            return 42

        action = RunProgram(program=prog())
        assert action.program is not None
        assert action.env is None

    def test_run_program_with_env(self) -> None:
        """RunProgram can have custom environment."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def prog() -> int:
            yield from PureEffect(42)
            return 42

        env: Environment = FrozenDict({"x": 1})
        action = RunProgram(program=prog(), env=env)
        assert action.env == env

    def test_modify_store(self) -> None:
        """ModifyStore holds store updates."""
        action = ModifyStore(updates={"counter": 10})
        assert action.updates == {"counter": 10}

    def test_resume_with_store(self) -> None:
        """ResumeWithStore holds value and store."""
        store = {"counter": 10}
        action = ResumeWithStore(value=42, store=store)
        assert action.value == 42
        assert action.store == store


class TestTaskActions:
    """Tests for task management action types."""

    def test_create_task(self) -> None:
        """CreateTask holds program and environment."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def prog() -> int:
            yield from PureEffect(42)
            return 42

        env: Environment = FrozenDict({"x": 1})
        action = CreateTask(program=prog(), env=env)
        assert action.program is not None
        assert action.env == env
        assert action.store_snapshot is None

    def test_create_task_with_store(self) -> None:
        """CreateTask can have initial store."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def prog() -> int:
            yield from PureEffect(42)
            return 42

        env: Environment = FrozenDict()
        store = {"counter": 0}
        action = CreateTask(program=prog(), env=env, store_snapshot=store)
        assert action.store_snapshot == store

    def test_create_tasks(self) -> None:
        """CreateTasks holds multiple programs."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def prog1() -> int:
            yield from PureEffect(1)
            return 1

        @do
        def prog2() -> int:
            yield from PureEffect(2)
            return 2

        env: Environment = FrozenDict()
        action = CreateTasks(programs=(prog1(), prog2()), env=env)
        assert len(action.programs) == 2

    def test_cancel_tasks(self) -> None:
        """CancelTasks holds task IDs to cancel."""
        t1, t2 = TaskId(1), TaskId(2)
        action = CancelTasks(task_ids=(t1, t2))
        assert action.task_ids == (t1, t2)


class TestWaitActions:
    """Tests for waiting action types."""

    def test_wait_on_future(self) -> None:
        """WaitOnFuture holds future ID."""
        future_id = FutureId(1)
        action = WaitOnFuture(future_id=future_id)
        assert action.future_id == future_id

    def test_wait_on_futures(self) -> None:
        """WaitOnFutures holds multiple future IDs."""
        f1, f2, f3 = FutureId(1), FutureId(2), FutureId(3)
        action = WaitOnFutures(future_ids=(f1, f2, f3))
        assert action.future_ids == (f1, f2, f3)

    def test_race_on_futures(self) -> None:
        """RaceOnFutures holds multiple future IDs."""
        f1, f2 = FutureId(1), FutureId(2)
        action = RaceOnFutures(future_ids=(f1, f2))
        assert action.future_ids == (f1, f2)

    def test_wait_until_time(self) -> None:
        """WaitUntilTime holds target timestamp."""
        action = WaitUntilTime(target_time=1000.0)
        assert action.target_time == 1000.0

    def test_wait_for_duration(self) -> None:
        """WaitForDuration holds seconds."""
        action = WaitForDuration(seconds=5.0)
        assert action.seconds == 5.0


class TestIOActions:
    """Tests for I/O action types."""

    def test_perform_io(self) -> None:
        """PerformIO holds an operation."""

        async def my_io() -> str:
            return "result"

        action = PerformIO(operation=my_io)
        assert action.operation is my_io

    def test_await_external(self) -> None:
        """AwaitExternal holds an awaitable."""

        async def coro() -> int:
            return 42

        awaitable = coro()
        action = AwaitExternal(awaitable=awaitable)
        assert action.awaitable is awaitable
        # Clean up
        awaitable.close()


class TestActionImmutability:
    """Tests that all action types are immutable."""

    def test_resume_immutable(self) -> None:
        """Resume is frozen."""
        action = Resume(value=42)
        with pytest.raises(AttributeError):
            action.value = 100  # type: ignore[misc]

    def test_create_task_immutable(self) -> None:
        """CreateTask is frozen."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def prog() -> int:
            yield from PureEffect(42)
            return 42

        env: Environment = FrozenDict()
        action = CreateTask(program=prog(), env=env)
        with pytest.raises(AttributeError):
            action.env = FrozenDict({"x": 1})  # type: ignore[misc]


class TestActionTypeAliases:
    """Tests for action type aliases."""

    def test_sync_action_types(self) -> None:
        """SyncAction includes expected types."""
        assert Resume(42).__class__.__name__ in str(SyncAction)
        assert ResumeError(ValueError()).__class__.__name__ in str(SyncAction)

    def test_task_action_types(self) -> None:
        """TaskAction includes expected types."""
        assert CancelTasks(task_ids=()).__class__.__name__ in str(TaskAction)

    def test_wait_action_types(self) -> None:
        """WaitAction includes expected types."""
        assert WaitOnFuture(FutureId(1)).__class__.__name__ in str(WaitAction)
