from __future__ import annotations

from datetime import datetime

from doeff.cesk.actions import (
    RunProgram,
    CreateTask,
    CreateTasks,
    CancelTasks,
    PerformIO,
    AwaitExternal,
    ScheduleAt,
    GetCurrentTime,
)
from doeff.cesk.types import TaskId, FutureId, Environment
from doeff._vendor import FrozenDict
from doeff.program import Program


def test_run_program_action():
    prog = Program.pure(42)
    action = RunProgram(prog)
    
    assert action.program is prog
    assert action.env is None


def test_run_program_action_with_env():
    prog = Program.pure(42)
    env = FrozenDict({"key": "value"})
    action = RunProgram(prog, env)
    
    assert action.program is prog
    assert action.env == env


def test_create_task_action():
    task_id = TaskId(1)
    prog = Program.pure(42)
    action = CreateTask(task_id, prog)
    
    assert action.task_id == task_id
    assert action.program is prog
    assert action.env is None
    assert action.parent_task_id is None


def test_create_task_action_with_parent():
    task_id = TaskId(1)
    parent_id = TaskId(0)
    prog = Program.pure(42)
    env = FrozenDict({"key": "value"})
    action = CreateTask(task_id, prog, env, parent_id)
    
    assert action.task_id == task_id
    assert action.program is prog
    assert action.env == env
    assert action.parent_task_id == parent_id


def test_create_tasks_action():
    task_specs = [
        (TaskId(1), Program.pure(1), None),
        (TaskId(2), Program.pure(2), FrozenDict({"k": "v"})),
    ]
    action = CreateTasks(task_specs)
    
    assert action.task_specs == task_specs
    assert action.parent_task_id is None


def test_create_tasks_action_with_parent():
    task_specs = [(TaskId(1), Program.pure(1), None)]
    parent_id = TaskId(0)
    action = CreateTasks(task_specs, parent_id)
    
    assert action.task_specs == task_specs
    assert action.parent_task_id == parent_id


def test_cancel_tasks_action():
    task_ids = [TaskId(1), TaskId(2), TaskId(3)]
    action = CancelTasks(task_ids)
    
    assert action.task_ids == task_ids


def test_perform_io_action():
    def io_func():
        return 42
    
    action = PerformIO(io_func)
    
    assert action.io_function is io_func
    assert action.args == ()
    assert action.kwargs is None


def test_perform_io_action_with_args():
    def io_func(a, b, c=10):
        return a + b + c
    
    action = PerformIO(io_func, (1, 2), {"c": 3})
    
    assert action.io_function is io_func
    assert action.args == (1, 2)
    assert action.kwargs == {"c": 3}


def test_await_external_action():
    async def async_func():
        return 42
    
    awaitable = async_func()
    future_id = FutureId(1)
    action = AwaitExternal(awaitable, future_id)
    
    assert action.awaitable is awaitable
    assert action.future_id == future_id
    
    awaitable.close()


def test_schedule_at_action():
    target = datetime(2024, 1, 1, 12, 0, 0)
    task_id = TaskId(1)
    action = ScheduleAt(target, task_id)
    
    assert action.target_time == target
    assert action.task_id == task_id


def test_get_current_time_action():
    action = GetCurrentTime()
    
    assert isinstance(action, GetCurrentTime)


def test_actions_are_frozen():
    import pytest
    
    prog = Program.pure(42)
    action = RunProgram(prog)
    
    with pytest.raises((AttributeError, TypeError)):
        action.program = Program.pure(100)  # type: ignore
