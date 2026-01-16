from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from doeff.cesk.state_new import (
    TaskStatus,
    Value,
    Error,
    EffectControl,
    ProgramControl,
    WaitingForFuture,
    WaitingForTime,
    GatherCondition,
    RaceCondition,
    TaskState,
    CESKState,
)
from doeff.cesk.types import TaskId, FutureId
from doeff._vendor import FrozenDict
from doeff.program import Program


def test_task_status_enum():
    assert TaskStatus.RUNNING != TaskStatus.BLOCKED
    assert TaskStatus.COMPLETED != TaskStatus.FAILED
    assert len(list(TaskStatus)) == 5


def test_value_control():
    val = Value(42)
    assert val.v == 42


def test_error_control():
    ex = ValueError("test error")
    err = Error(ex)
    assert err.ex == ex
    assert err.captured_traceback is None


def test_effect_control():
    from doeff.effects import get
    
    effect = get("key")
    ctrl = EffectControl(effect)
    assert ctrl.effect == effect


def test_program_control():
    prog = Program.pure(42)
    ctrl = ProgramControl(prog)
    assert ctrl.program == prog


def test_waiting_for_future_condition():
    future_id = FutureId(1)
    cond = WaitingForFuture(future_id)
    assert cond.future_id == future_id


def test_waiting_for_time_condition():
    target = datetime.now() + timedelta(seconds=10)
    cond = WaitingForTime(target)
    assert cond.target_time == target


def test_gather_condition():
    task_ids = (TaskId(1), TaskId(2), TaskId(3))
    results = (10, 20)
    cond = GatherCondition(task_ids, results)
    assert cond.child_task_ids == task_ids
    assert cond.results == results


def test_race_condition():
    task_ids = (TaskId(1), TaskId(2))
    cond = RaceCondition(task_ids)
    assert cond.child_task_ids == task_ids


def test_task_state_creation():
    task_id = TaskId(0)
    prog = Program.pure(42)
    env = FrozenDict({"key": "value"})
    
    task = TaskState(
        task_id=task_id,
        control=ProgramControl(prog),
        environment=env,
        kontinuation=[],
        status=TaskStatus.RUNNING,
    )
    
    assert task.task_id == task_id
    assert isinstance(task.control, ProgramControl)
    assert task.environment == env
    assert task.kontinuation == []
    assert task.status == TaskStatus.RUNNING
    assert task.condition is None
    assert task.parent_task_id is None


def test_task_state_with_condition():
    task_id = TaskId(0)
    future_id = FutureId(1)
    
    task = TaskState(
        task_id=task_id,
        control=Value(None),
        environment=FrozenDict(),
        kontinuation=[],
        status=TaskStatus.BLOCKED,
        condition=WaitingForFuture(future_id),
    )
    
    assert task.status == TaskStatus.BLOCKED
    assert isinstance(task.condition, WaitingForFuture)
    assert task.condition.future_id == future_id


def test_cesk_state_initial():
    prog = Program.pure(42)
    task_id = TaskId(0)
    env = {"key": "value"}
    store = {"state_key": "state_value"}
    
    state = CESKState.initial(prog, task_id, env, store)
    
    assert len(state.tasks) == 1
    assert task_id in state.tasks
    assert state.active_task_id == task_id
    assert state.store == store
    assert state.futures == {}
    
    task = state.tasks[task_id]
    assert task.task_id == task_id
    assert isinstance(task.control, ProgramControl)
    assert task.environment["key"] == "value"
    assert task.status == TaskStatus.RUNNING


def test_cesk_state_initial_with_defaults():
    prog = Program.pure(42)
    task_id = TaskId(0)
    
    state = CESKState.initial(prog, task_id)
    
    assert len(state.tasks) == 1
    assert task_id in state.tasks
    assert state.active_task_id == task_id
    assert state.store == {}
    assert state.futures == {}


def test_cesk_state_with_task():
    prog = Program.pure(42)
    task_id1 = TaskId(0)
    task_id2 = TaskId(1)
    
    state = CESKState.initial(prog, task_id1)
    
    new_task = TaskState(
        task_id=task_id2,
        control=ProgramControl(Program.pure(100)),
        environment=FrozenDict(),
        kontinuation=[],
    )
    
    new_state = state.with_task(task_id2, new_task)
    
    assert len(new_state.tasks) == 2
    assert task_id1 in new_state.tasks
    assert task_id2 in new_state.tasks
    assert new_state.tasks[task_id2] == new_task
    assert len(state.tasks) == 1


def test_cesk_state_with_active_task():
    prog = Program.pure(42)
    task_id1 = TaskId(0)
    task_id2 = TaskId(1)
    
    state = CESKState.initial(prog, task_id1)
    assert state.active_task_id == task_id1
    
    new_state = state.with_active_task(task_id2)
    assert new_state.active_task_id == task_id2
    assert state.active_task_id == task_id1


def test_cesk_state_with_store():
    prog = Program.pure(42)
    task_id = TaskId(0)
    
    state = CESKState.initial(prog, task_id, store={"old": "value"})
    new_store = {"new": "value", "count": 10}
    
    new_state = state.with_store(new_store)
    
    assert new_state.store == new_store
    assert state.store == {"old": "value"}


def test_cesk_state_with_future():
    prog = Program.pure(42)
    task_id = TaskId(0)
    future_id = FutureId(1)
    
    state = CESKState.initial(prog, task_id)
    assert future_id not in state.futures
    
    new_state = state.with_future(future_id, "result_value")
    
    assert future_id in new_state.futures
    assert new_state.futures[future_id] == "result_value"
    assert future_id not in state.futures


def test_cesk_state_get_active_task():
    prog = Program.pure(42)
    task_id = TaskId(0)
    
    state = CESKState.initial(prog, task_id)
    
    active_task = state.get_active_task()
    assert active_task is not None
    assert active_task.task_id == task_id


def test_cesk_state_get_active_task_when_none():
    prog = Program.pure(42)
    task_id = TaskId(0)
    
    state = CESKState.initial(prog, task_id)
    state_no_active = state.with_active_task(None)
    
    active_task = state_no_active.get_active_task()
    assert active_task is None


def test_cesk_state_remove_task():
    prog = Program.pure(42)
    task_id1 = TaskId(0)
    task_id2 = TaskId(1)
    
    state = CESKState.initial(prog, task_id1)
    new_task = TaskState(
        task_id=task_id2,
        control=Value(100),
        environment=FrozenDict(),
        kontinuation=[],
    )
    state = state.with_task(task_id2, new_task)
    
    assert len(state.tasks) == 2
    
    new_state = state.remove_task(task_id2)
    
    assert len(new_state.tasks) == 1
    assert task_id1 in new_state.tasks
    assert task_id2 not in new_state.tasks
    assert len(state.tasks) == 2


def test_cesk_state_remove_active_task():
    prog = Program.pure(42)
    task_id = TaskId(0)
    
    state = CESKState.initial(prog, task_id)
    assert state.active_task_id == task_id
    
    new_state = state.remove_task(task_id)
    
    assert len(new_state.tasks) == 0
    assert new_state.active_task_id is None


def test_cesk_state_immutability():
    prog = Program.pure(42)
    task_id = TaskId(0)
    
    state1 = CESKState.initial(prog, task_id)
    state2 = state1.with_store({"new": "store"})
    
    assert state1.store != state2.store
    assert id(state1) != id(state2)


def test_task_state_frozen():
    task_id = TaskId(0)
    
    task = TaskState(
        task_id=task_id,
        control=Value(42),
        environment=FrozenDict(),
        kontinuation=[],
    )
    
    with pytest.raises((AttributeError, TypeError)):
        task.status = TaskStatus.COMPLETED  # type: ignore


def test_cesk_state_frozen():
    prog = Program.pure(42)
    task_id = TaskId(0)
    
    state = CESKState.initial(prog, task_id)
    
    with pytest.raises((AttributeError, TypeError)):
        state.active_task_id = TaskId(1)  # type: ignore
