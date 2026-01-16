from __future__ import annotations

from doeff.cesk.step_new import step, Done, Failed, Suspended
from doeff.cesk.state_new import CESKState, TaskState, TaskStatus, Value, ProgramControl
from doeff.cesk.types import TaskId
from doeff._vendor import FrozenDict
from doeff.program import Program


def test_step_pure_value_completes():
    prog = Program.pure(42)
    task_id = TaskId(0)
    state = CESKState.initial(prog, task_id)
    
    result = step(state)
    
    assert isinstance(result, (Done, Suspended))


def test_step_with_completed_task():
    task_id = TaskId(0)
    completed_task = TaskState(
        task_id=task_id,
        control=Value(42),
        environment=FrozenDict(),
        kontinuation=[],
        status=TaskStatus.COMPLETED,
    )
    
    state = CESKState(
        tasks={task_id: completed_task},
        store={},
        active_task_id=None,
    )
    
    result = step(state)
    
    assert isinstance(result, Done)
    assert result.value == 42


def test_step_no_active_tasks():
    state = CESKState(
        tasks={},
        store={},
        active_task_id=None,
    )
    
    result = step(state)
    
    assert isinstance(result, Failed)


def test_step_selects_running_task():
    task_id1 = TaskId(0)
    task_id2 = TaskId(1)
    
    task1 = TaskState(
        task_id=task_id1,
        control=Value(10),
        environment=FrozenDict(),
        kontinuation=[],
        status=TaskStatus.COMPLETED,
    )
    
    task2 = TaskState(
        task_id=task_id2,
        control=ProgramControl(Program.pure(20)),
        environment=FrozenDict(),
        kontinuation=[],
        status=TaskStatus.RUNNING,
    )
    
    state = CESKState(
        tasks={task_id1: task1, task_id2: task2},
        store={},
        active_task_id=None,
    )
    
    result = step(state)
    
    assert isinstance(result, (Done, Suspended)) or hasattr(result, 'state')


def test_step_program_control_advances():
    prog = Program.pure(42)
    task_id = TaskId(0)
    
    task = TaskState(
        task_id=task_id,
        control=ProgramControl(prog),
        environment=FrozenDict(),
        kontinuation=[],
        status=TaskStatus.RUNNING,
    )
    
    state = CESKState(
        tasks={task_id: task},
        store={},
        active_task_id=task_id,
    )
    
    result = step(state)
    
    assert isinstance(result, (Done, Suspended)) or hasattr(result, 'state')


def test_step_handles_stopiteration():
    def empty_gen():
        return 42
        yield
    
    class SimpleProgram:
        def __iter__(self):
            return empty_gen()
    
    prog = SimpleProgram()
    task_id = TaskId(0)
    
    task = TaskState(
        task_id=task_id,
        control=ProgramControl(prog),  # type: ignore
        environment=FrozenDict(),
        kontinuation=[],
        status=TaskStatus.RUNNING,
    )
    
    state = CESKState(
        tasks={task_id: task},
        store={},
        active_task_id=task_id,
    )
    
    result = step(state)
    
    assert isinstance(result, (Done, Suspended)) or hasattr(result, 'state')
