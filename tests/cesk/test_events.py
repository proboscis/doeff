"""Unit tests for doeff.cesk.events module."""

from __future__ import annotations

import pytest
from doeff._vendor import FrozenDict
from doeff.cesk.events import (
    AwaitRequested,
    BlockingEvent,
    CreationEvent,
    Event,
    FutureEvent,
    FutureRejected,
    FutureResolved,
    IOEvent,
    IORequested,
    SchedulingEvent,
    TaskBlocked,
    TaskCancelled,
    TaskCreated,
    TaskDone,
    TaskFailed,
    TaskRacing,
    TaskReady,
    TasksCreated,
    TaskStateEvent,
    TaskWaitingForDuration,
    TaskWaitingOnFuture,
    TaskWaitingOnFutures,
    TaskWaitingUntilTime,
    TaskYielded,
)
from doeff.cesk.types import Environment, FutureId, TaskId


class TestTaskLifecycleEvents:
    """Tests for task lifecycle event types."""

    def test_task_done(self) -> None:
        """TaskDone holds task ID, value, and store."""
        task_id = TaskId(1)
        store = {"counter": 10}
        event = TaskDone(task_id=task_id, value=42, store=store)

        assert event.task_id == task_id
        assert event.value == 42
        assert event.store == store

    def test_task_failed(self) -> None:
        """TaskFailed holds task ID, error, and store."""
        task_id = TaskId(1)
        ex = ValueError("test error")
        store = {"counter": 10}
        event = TaskFailed(task_id=task_id, error=ex, store=store)

        assert event.task_id == task_id
        assert event.error is ex
        assert event.store == store
        assert event.captured_traceback is None

    def test_task_cancelled(self) -> None:
        """TaskCancelled holds task ID."""
        task_id = TaskId(1)
        event = TaskCancelled(task_id=task_id)

        assert event.task_id == task_id


class TestTaskBlockingEvents:
    """Tests for task blocking event types."""

    def test_task_blocked(self) -> None:
        """TaskBlocked holds task ID and I/O operation."""
        task_id = TaskId(1)

        async def my_io():
            return "result"

        event = TaskBlocked(task_id=task_id, io_operation=my_io)

        assert event.task_id == task_id
        assert event.io_operation is my_io

    def test_task_waiting_on_future(self) -> None:
        """TaskWaitingOnFuture holds task and future IDs."""
        task_id = TaskId(1)
        future_id = FutureId(2)
        event = TaskWaitingOnFuture(task_id=task_id, future_id=future_id)

        assert event.task_id == task_id
        assert event.future_id == future_id

    def test_task_waiting_on_futures(self) -> None:
        """TaskWaitingOnFutures holds task ID and multiple future IDs."""
        task_id = TaskId(1)
        f1, f2, f3 = FutureId(1), FutureId(2), FutureId(3)
        event = TaskWaitingOnFutures(task_id=task_id, future_ids=(f1, f2, f3))

        assert event.task_id == task_id
        assert event.future_ids == (f1, f2, f3)

    def test_task_racing(self) -> None:
        """TaskRacing holds task ID and future IDs to race."""
        task_id = TaskId(1)
        f1, f2 = FutureId(1), FutureId(2)
        event = TaskRacing(task_id=task_id, future_ids=(f1, f2))

        assert event.task_id == task_id
        assert event.future_ids == (f1, f2)

    def test_task_waiting_until_time(self) -> None:
        """TaskWaitingUntilTime holds task ID and target time."""
        task_id = TaskId(1)
        event = TaskWaitingUntilTime(task_id=task_id, target_time=1000.0)

        assert event.task_id == task_id
        assert event.target_time == 1000.0

    def test_task_waiting_for_duration(self) -> None:
        """TaskWaitingForDuration holds task ID and duration."""
        task_id = TaskId(1)
        event = TaskWaitingForDuration(task_id=task_id, seconds=5.0)

        assert event.task_id == task_id
        assert event.seconds == 5.0


class TestTaskCreationEvents:
    """Tests for task creation event types."""

    def test_task_created(self) -> None:
        """TaskCreated holds all task creation info."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def prog() -> int:
            yield from PureEffect(42)
            return 42

        task_id = TaskId(1)
        future_id = FutureId(0)
        env: Environment = FrozenDict({"x": 1})

        event = TaskCreated(
            task_id=task_id,
            future_id=future_id,
            program=prog(),
            env=env,
        )

        assert event.task_id == task_id
        assert event.future_id == future_id
        assert event.program is not None
        assert event.env == env
        assert event.store is None

    def test_task_created_with_store(self) -> None:
        """TaskCreated can have initial store."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def prog() -> int:
            yield from PureEffect(42)
            return 42

        store = {"counter": 0}
        event = TaskCreated(
            task_id=TaskId(1),
            future_id=FutureId(0),
            program=prog(),
            env=FrozenDict(),
            store=store,
        )

        assert event.store == store

    def test_tasks_created(self) -> None:
        """TasksCreated holds multiple TaskCreated events."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def prog() -> int:
            yield from PureEffect(42)
            return 42

        env: Environment = FrozenDict()
        t1 = TaskCreated(
            task_id=TaskId(1),
            future_id=FutureId(0),
            program=prog(),
            env=env,
        )
        t2 = TaskCreated(
            task_id=TaskId(2),
            future_id=FutureId(1),
            program=prog(),
            env=env,
        )

        event = TasksCreated(tasks=(t1, t2))

        assert len(event.tasks) == 2
        assert event.tasks[0].task_id == TaskId(1)
        assert event.tasks[1].task_id == TaskId(2)


class TestFutureEvents:
    """Tests for future event types."""

    def test_future_resolved(self) -> None:
        """FutureResolved holds future ID and value."""
        future_id = FutureId(1)
        event = FutureResolved(future_id=future_id, value=42)

        assert event.future_id == future_id
        assert event.value == 42

    def test_future_rejected(self) -> None:
        """FutureRejected holds future ID and error."""
        future_id = FutureId(1)
        ex = ValueError("test error")
        event = FutureRejected(future_id=future_id, error=ex)

        assert event.future_id == future_id
        assert event.error is ex
        assert event.captured_traceback is None


class TestIOEvents:
    """Tests for I/O event types."""

    def test_io_requested(self) -> None:
        """IORequested holds task ID and operation."""
        task_id = TaskId(1)

        async def my_io():
            return "result"

        event = IORequested(task_id=task_id, operation=my_io)

        assert event.task_id == task_id
        assert event.operation is my_io

    def test_await_requested(self) -> None:
        """AwaitRequested holds task ID and awaitable."""
        task_id = TaskId(1)

        async def coro():
            return 42

        awaitable = coro()
        event = AwaitRequested(task_id=task_id, awaitable=awaitable)

        assert event.task_id == task_id
        assert event.awaitable is awaitable
        # Clean up
        awaitable.close()


class TestSchedulingEvents:
    """Tests for scheduling event types."""

    def test_task_ready(self) -> None:
        """TaskReady holds task ID."""
        task_id = TaskId(1)
        event = TaskReady(task_id=task_id)

        assert event.task_id == task_id

    def test_task_yielded(self) -> None:
        """TaskYielded holds task ID."""
        task_id = TaskId(1)
        event = TaskYielded(task_id=task_id)

        assert event.task_id == task_id


class TestEventImmutability:
    """Tests that all event types are immutable."""

    def test_task_done_immutable(self) -> None:
        """TaskDone is frozen."""
        event = TaskDone(task_id=TaskId(1), value=42, store={})
        with pytest.raises(AttributeError):
            event.value = 100  # type: ignore[misc]

    def test_task_created_immutable(self) -> None:
        """TaskCreated is frozen."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def prog() -> int:
            yield from PureEffect(42)
            return 42

        event = TaskCreated(
            task_id=TaskId(1),
            future_id=FutureId(0),
            program=prog(),
            env=FrozenDict(),
        )
        with pytest.raises(AttributeError):
            event.task_id = TaskId(2)  # type: ignore[misc]


class TestEventTypeAliases:
    """Tests for event type aliases."""

    def test_task_state_event_types(self) -> None:
        """TaskStateEvent includes expected types."""
        assert TaskDone(TaskId(1), 42, {}).__class__.__name__ in str(TaskStateEvent)
        assert TaskFailed(TaskId(1), ValueError(), {}).__class__.__name__ in str(
            TaskStateEvent
        )
        assert TaskCancelled(TaskId(1)).__class__.__name__ in str(TaskStateEvent)

    def test_blocking_event_types(self) -> None:
        """BlockingEvent includes expected types."""
        assert TaskWaitingOnFuture(TaskId(1), FutureId(1)).__class__.__name__ in str(
            BlockingEvent
        )

    def test_future_event_types(self) -> None:
        """FutureEvent includes expected types."""
        assert FutureResolved(FutureId(1), 42).__class__.__name__ in str(FutureEvent)
        assert FutureRejected(FutureId(1), ValueError()).__class__.__name__ in str(
            FutureEvent
        )
