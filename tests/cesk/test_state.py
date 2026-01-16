from datetime import datetime, timedelta

import pytest

from doeff._vendor import FrozenDict
from doeff.cesk.types import FutureId, TaskId
from doeff.cesk.state import (
    Control,
    EffectControl,
    Error,
    ProgramControl,
    Value,
)
from doeff.cesk.unified_state import (
    Condition,
    TaskState,
    TaskStatus,
    UnifiedCESKState as CESKState,
    WaitingForAll,
    WaitingForAny,
    WaitingForFuture,
    WaitingForIO,
    WaitingForTime,
)
from doeff.program import Program


class TestControlTypes:
    def test_value_holds_any_value(self):
        v = Value(42)
        assert v.v == 42
    
    def test_value_is_frozen(self):
        v = Value("test")
        with pytest.raises(AttributeError):
            v.v = "changed"  # type: ignore[misc]
    
    def test_error_holds_exception(self):
        ex = ValueError("test")
        e = Error(ex)
        assert e.ex is ex
        assert e.captured_traceback is None
    
    def test_error_with_traceback(self):
        ex = RuntimeError("failed")
        e = Error(ex, captured_traceback=None)
        assert e.ex is ex
    
    def test_effect_control_holds_effect(self):
        from doeff.effects import AskEffect
        effect = AskEffect("key")
        ctrl = EffectControl(effect)
        assert ctrl.effect is effect
    
    def test_program_control_holds_program(self):
        prog = Program.pure(42)
        ctrl = ProgramControl(prog)
        assert ctrl.program is prog


class TestTaskStatus:
    def test_status_values(self):
        assert TaskStatus.RUNNING.name == "RUNNING"
        assert TaskStatus.BLOCKED.name == "BLOCKED"
        assert TaskStatus.COMPLETED.name == "COMPLETED"
        assert TaskStatus.FAILED.name == "FAILED"


class TestConditionTypes:
    def test_waiting_for_future(self):
        fid = FutureId(1)
        cond = WaitingForFuture(fid)
        assert cond.future_id == fid
    
    def test_waiting_for_time(self):
        target = datetime(2025, 1, 16, 12, 0, 0)
        cond = WaitingForTime(target)
        assert cond.target == target
    
    def test_waiting_for_io(self):
        cond = WaitingForIO(io_id=42)
        assert cond.io_id == 42
    
    def test_waiting_for_any(self):
        tids = frozenset([TaskId(1), TaskId(2)])
        cond = WaitingForAny(tids)
        assert cond.task_ids == tids
    
    def test_waiting_for_all(self):
        tids = frozenset([TaskId(1), TaskId(2), TaskId(3)])
        cond = WaitingForAll(tids)
        assert cond.task_ids == tids


class TestTaskState:
    def test_initial_creates_running_task(self):
        prog = Program.pure(42)
        task = TaskState.initial(TaskId(0), prog)
        
        assert task.task_id == TaskId(0)
        assert isinstance(task.control, ProgramControl)
        assert task.env == FrozenDict()
        assert task.kontinuation == []
        assert task.status == TaskStatus.RUNNING
        assert task.condition is None
    
    def test_initial_with_env(self):
        prog = Program.pure(42)
        env = {"key": "value"}
        task = TaskState.initial(TaskId(1), prog, env=env)
        
        assert task.env == FrozenDict({"key": "value"})
    
    def test_initial_with_parent(self):
        prog = Program.pure(42)
        task = TaskState.initial(TaskId(1), prog, parent_id=TaskId(0))
        
        assert task.parent_id == TaskId(0)
    
    def test_with_control_returns_new_task(self):
        prog = Program.pure(42)
        task = TaskState.initial(TaskId(0), prog)
        
        new_control = Value(100)
        new_task = task.with_control(new_control)
        
        assert new_task.control == new_control
        assert task.control != new_control
        assert new_task.task_id == task.task_id
    
    def test_with_status_returns_new_task(self):
        prog = Program.pure(42)
        task = TaskState.initial(TaskId(0), prog)
        
        cond = WaitingForFuture(FutureId(1))
        new_task = task.with_status(TaskStatus.BLOCKED, cond)
        
        assert new_task.status == TaskStatus.BLOCKED
        assert new_task.condition == cond
        assert task.status == TaskStatus.RUNNING
    
    def test_with_env_returns_new_task(self):
        prog = Program.pure(42)
        task = TaskState.initial(TaskId(0), prog)
        
        new_env = FrozenDict({"new": "env"})
        new_task = task.with_env(new_env)
        
        assert new_task.env == new_env
        assert task.env == FrozenDict()
    
    def test_push_frame_adds_to_front(self):
        from doeff.cesk.frames import LocalFrame
        
        prog = Program.pure(42)
        task = TaskState.initial(TaskId(0), prog)
        
        frame = LocalFrame(restore_env=FrozenDict())
        new_task = task.push_frame(frame)
        
        assert len(new_task.kontinuation) == 1
        assert new_task.kontinuation[0] is frame
        assert len(task.kontinuation) == 0
    
    def test_pop_frame_returns_first_frame(self):
        from doeff.cesk.frames import LocalFrame
        
        prog = Program.pure(42)
        task = TaskState.initial(TaskId(0), prog)
        
        frame1 = LocalFrame(restore_env=FrozenDict({"a": 1}))
        frame2 = LocalFrame(restore_env=FrozenDict({"b": 2}))
        
        task = task.push_frame(frame2)
        task = task.push_frame(frame1)
        
        popped, new_task = task.pop_frame()
        
        assert popped is frame1
        assert len(new_task.kontinuation) == 1
        assert new_task.kontinuation[0] is frame2
    
    def test_pop_frame_empty_returns_none(self):
        prog = Program.pure(42)
        task = TaskState.initial(TaskId(0), prog)
        
        popped, new_task = task.pop_frame()
        
        assert popped is None
        assert new_task is task


class TestCESKState:
    def test_initial_creates_main_task(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        assert state.main_task_id == TaskId(0)
        assert len(state.tasks) == 1
        assert TaskId(0) in state.tasks
        assert state.store == {}
    
    def test_initial_with_env(self):
        prog = Program.pure(42)
        env = {"key": "value"}
        state = CESKState.initial(prog, env=env)
        
        main_task = state.get_task(TaskId(0))
        assert main_task is not None
        assert main_task.env == FrozenDict({"key": "value"})
    
    def test_initial_with_store(self):
        prog = Program.pure(42)
        store = {"counter": 0}
        state = CESKState.initial(prog, store=store)
        
        assert state.store == {"counter": 0}
    
    def test_initial_with_time(self):
        prog = Program.pure(42)
        now = datetime.now()
        state = CESKState.initial(prog, current_time=now)
        
        assert state.current_time == now
    
    def test_get_task_returns_task(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        task = state.get_task(TaskId(0))
        assert task is not None
        assert task.task_id == TaskId(0)
    
    def test_get_task_returns_none_for_missing(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        task = state.get_task(TaskId(999))
        assert task is None
    
    def test_update_task_returns_new_state(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        task = state.get_task(TaskId(0))
        assert task is not None
        updated_task = task.with_control(Value(100))
        new_state = state.update_task(updated_task)
        
        assert new_state is not state
        assert new_state.get_task(TaskId(0)).control == Value(100)
        assert state.get_task(TaskId(0)).control != Value(100)
    
    def test_add_task_adds_new_task(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        new_task = TaskState.initial(
            state.id_gen.next_task_id(),
            Program.pure(100),
            parent_id=TaskId(0),
        )
        new_state = state.add_task(new_task)
        
        assert len(new_state.tasks) == 2
        assert new_task.task_id in new_state.tasks
    
    def test_remove_task_removes_task(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        new_task = TaskState.initial(
            state.id_gen.next_task_id(),
            Program.pure(100),
        )
        state = state.add_task(new_task)
        assert len(state.tasks) == 2
        
        state = state.remove_task(new_task.task_id)
        assert len(state.tasks) == 1
        assert new_task.task_id not in state.tasks
    
    def test_complete_task_marks_completed(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        state = state.complete_task(TaskId(0), "result")
        
        task = state.get_task(TaskId(0))
        assert task.status == TaskStatus.COMPLETED
        assert state.completed_values[TaskId(0)] == "result"
    
    def test_fail_task_marks_failed(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        error = ValueError("failed")
        state = state.fail_task(TaskId(0), error)
        
        task = state.get_task(TaskId(0))
        assert task.status == TaskStatus.FAILED
        assert state.failed_errors[TaskId(0)] is error
    
    def test_with_store_returns_new_state(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        new_state = state.with_store({"new": "store"})
        
        assert new_state.store == {"new": "store"}
        assert state.store == {}
    
    def test_with_time_returns_new_state(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        now = datetime.now()
        new_state = state.with_time(now)
        
        assert new_state.current_time == now
        assert state.current_time is None
    
    def test_set_future_stores_value(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        fid = state.id_gen.next_future_id()
        state = state.set_future(fid, "future_value")
        
        assert state.future_values[fid] == "future_value"
    
    def test_runnable_tasks_returns_running(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        runnable = state.runnable_tasks()
        assert runnable == [TaskId(0)]
        
        task = state.get_task(TaskId(0))
        blocked_task = task.with_status(
            TaskStatus.BLOCKED,
            WaitingForFuture(FutureId(0)),
        )
        state = state.update_task(blocked_task)
        
        runnable = state.runnable_tasks()
        assert runnable == []
    
    def test_blocked_tasks_returns_blocked_with_condition(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        task = state.get_task(TaskId(0))
        cond = WaitingForTime(datetime(2025, 1, 16))
        blocked_task = task.with_status(TaskStatus.BLOCKED, cond)
        state = state.update_task(blocked_task)
        
        blocked = state.blocked_tasks()
        assert len(blocked) == 1
        assert blocked[0] == (TaskId(0), cond)
    
    def test_is_complete_when_main_completed(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        assert not state.is_complete()
        
        state = state.complete_task(TaskId(0), "result")
        assert state.is_complete()
    
    def test_is_complete_when_main_failed(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        state = state.fail_task(TaskId(0), ValueError("error"))
        assert state.is_complete()
    
    def test_main_result_returns_success(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        assert state.main_result() is None
        
        state = state.complete_task(TaskId(0), "success")
        result = state.main_result()
        
        assert result == ("success", True)
    
    def test_main_result_returns_failure(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        error = ValueError("error")
        state = state.fail_task(TaskId(0), error)
        result = state.main_result()
        
        assert result == (error, False)
