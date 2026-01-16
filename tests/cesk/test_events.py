from datetime import datetime

import pytest

from doeff.cesk.events import (
    AllTasksComplete,
    EffectSuspended,
    Event,
    ExternalAwait,
    IORequested,
    NeedsTimeAdvance,
    Stepped,
    TaskBlocked,
    TaskCompleted,
    TaskFailed,
    TasksCreated,
    TimeWait,
)
from doeff.cesk.state import CESKState
from doeff.cesk.types import FutureId, TaskId
from doeff.effects import AskEffect
from doeff.program import Program


@pytest.fixture
def sample_state():
    prog = Program.pure(42)
    return CESKState.initial(prog)


class TestTaskCompleted:
    def test_task_completed_holds_data(self, sample_state):
        event = TaskCompleted(
            task_id=TaskId(0),
            value="result",
            state=sample_state,
        )
        assert event.task_id == TaskId(0)
        assert event.value == "result"
        assert event.state is sample_state


class TestTaskFailed:
    def test_task_failed_holds_error(self, sample_state):
        error = ValueError("test")
        event = TaskFailed(
            task_id=TaskId(0),
            error=error,
            state=sample_state,
        )
        assert event.task_id == TaskId(0)
        assert event.error is error


class TestTaskBlocked:
    def test_task_blocked_holds_state(self, sample_state):
        event = TaskBlocked(
            task_id=TaskId(0),
            state=sample_state,
        )
        assert event.task_id == TaskId(0)


class TestEffectSuspended:
    def test_effect_suspended_holds_effect(self, sample_state):
        effect = AskEffect("key")
        event = EffectSuspended(
            task_id=TaskId(0),
            effect=effect,
            state=sample_state,
        )
        assert event.task_id == TaskId(0)
        assert event.effect is effect


class TestIORequested:
    def test_io_requested_holds_io_data(self, sample_state):
        def io_fn():
            return "result"
        
        event = IORequested(
            task_id=TaskId(0),
            io_callable=io_fn,
            io_id=42,
            state=sample_state,
        )
        assert event.io_callable is io_fn
        assert event.io_id == 42


class TestExternalAwait:
    def test_external_await_holds_awaitable(self, sample_state):
        async def coro():
            return "result"
        
        aw = coro()
        event = ExternalAwait(
            task_id=TaskId(0),
            awaitable=aw,
            future_id=FutureId(1),
            state=sample_state,
        )
        assert event.awaitable is aw
        assert event.future_id == FutureId(1)
        aw.close()


class TestTimeWait:
    def test_time_wait_holds_target(self, sample_state):
        target = datetime(2025, 1, 16, 12, 0, 0)
        event = TimeWait(
            task_id=TaskId(0),
            target=target,
            state=sample_state,
        )
        assert event.target == target


class TestTasksCreated:
    def test_tasks_created_holds_ids(self, sample_state):
        event = TasksCreated(
            parent_id=TaskId(0),
            child_ids=(TaskId(1), TaskId(2), TaskId(3)),
            state=sample_state,
        )
        assert event.parent_id == TaskId(0)
        assert event.child_ids == (TaskId(1), TaskId(2), TaskId(3))


class TestAllTasksComplete:
    def test_all_tasks_complete_holds_state(self, sample_state):
        event = AllTasksComplete(state=sample_state)
        assert event.state is sample_state


class TestNeedsTimeAdvance:
    def test_needs_time_advance_holds_earliest_wake(self, sample_state):
        wake = datetime(2025, 1, 16, 13, 0, 0)
        event = NeedsTimeAdvance(
            earliest_wake=wake,
            state=sample_state,
        )
        assert event.earliest_wake == wake


class TestStepped:
    def test_stepped_holds_state(self, sample_state):
        event = Stepped(state=sample_state)
        assert event.state is sample_state
