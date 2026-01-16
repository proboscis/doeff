from __future__ import annotations

from typing import TYPE_CHECKING

from doeff._types_internal import EffectBase
from doeff.cesk.state_new import (
    CESKState,
    TaskState,
    TaskStatus,
    Value,
    Error,
    EffectControl,
    ProgramControl,
)
from doeff.cesk.frames_new import PopFrame, Continue
from doeff.cesk.actions import Action

if TYPE_CHECKING:
    from doeff.program import Program


class StepResult:
    pass


class Done(StepResult):
    def __init__(self, value, store):
        self.value = value
        self.store = store


class Failed(StepResult):
    def __init__(self, error, store, captured_traceback=None):
        self.error = error
        self.store = store
        self.captured_traceback = captured_traceback


class Suspended(StepResult):
    def __init__(self, effect, state):
        self.effect = effect
        self.state = state


class NeedsAction(StepResult):
    def __init__(self, action: Action, state: CESKState):
        self.action = action
        self.state = state


def step(state: CESKState) -> StepResult:
    active_task = state.get_active_task()
    
    if active_task is None:
        running_tasks = [t for t in state.tasks.values() if t.status == TaskStatus.RUNNING]
        if not running_tasks:
            completed_tasks = [t for t in state.tasks.values() if t.status == TaskStatus.COMPLETED]
            if completed_tasks:
                return Done(completed_tasks[0].control.v if isinstance(completed_tasks[0].control, Value) else None, state.store)  # type: ignore
            return Failed(RuntimeError("No active tasks"), state.store)
        
        next_task_id = running_tasks[0].task_id
        new_state = state.with_active_task(next_task_id)
        return step(new_state)
    
    control = active_task.control
    kontinuation = active_task.kontinuation
    
    if isinstance(control, Value) and not kontinuation:
        new_task = TaskState(
            task_id=active_task.task_id,
            control=control,
            environment=active_task.environment,
            kontinuation=[],
            status=TaskStatus.COMPLETED,
        )
        new_state = state.with_task(active_task.task_id, new_task)
        
        if active_task.parent_task_id is not None:
            parent_task = state.tasks.get(active_task.parent_task_id)
            if parent_task and parent_task.kontinuation:
                frame = parent_task.kontinuation[0]
                result_tuple = frame.on_child_done(active_task.task_id, control.v)
                if result_tuple:
                    new_value, frame_result = result_tuple
                    if isinstance(frame_result, PopFrame):
                        new_parent = TaskState(
                            task_id=parent_task.task_id,
                            control=Value(new_value),
                            environment=parent_task.environment,
                            kontinuation=parent_task.kontinuation[1:],
                            status=TaskStatus.RUNNING,
                        )
                        new_state = new_state.with_task(parent_task.task_id, new_parent)
                        new_state = new_state.with_active_task(parent_task.task_id)
                        return step(new_state)
        
        new_state = new_state.with_active_task(None)
        return step(new_state)
    
    if isinstance(control, Error) and not kontinuation:
        new_task = TaskState(
            task_id=active_task.task_id,
            control=control,
            environment=active_task.environment,
            kontinuation=[],
            status=TaskStatus.FAILED,
        )
        new_state = state.with_task(active_task.task_id, new_task).with_active_task(None)
        return Failed(control.ex, state.store, control.captured_traceback)
    
    if isinstance(control, EffectControl):
        return Suspended(control.effect, state)
    
    if isinstance(control, ProgramControl):
        from doeff.program import ProgramBase
        
        program = control.program
        try:
            if hasattr(program, '__iter__'):
                gen = iter(program)
            elif isinstance(program, ProgramBase):
                gen = program.__iter__()
            else:
                gen = iter([])
            
            item = next(gen)
            
            if isinstance(item, EffectBase):
                new_control = EffectControl(item)
            elif isinstance(item, ProgramBase):
                new_control = ProgramControl(item)
            else:
                raise TypeError(f"Program yielded unexpected type: {type(item).__name__}")
            
            from doeff.cesk.frames_new import ReturnFrame
            new_frame = ReturnFrame(gen, active_task.environment)
            new_task = TaskState(
                task_id=active_task.task_id,
                control=new_control,
                environment=active_task.environment,
                kontinuation=[new_frame] + kontinuation,
                status=TaskStatus.RUNNING,
            )
            new_state = state.with_task(active_task.task_id, new_task)
            return step(new_state)
            
        except StopIteration as e:
            new_task = TaskState(
                task_id=active_task.task_id,
                control=Value(e.value),
                environment=active_task.environment,
                kontinuation=kontinuation,
                status=TaskStatus.RUNNING,
            )
            new_state = state.with_task(active_task.task_id, new_task)
            return step(new_state)
        except Exception as ex:
            new_task = TaskState(
                task_id=active_task.task_id,
                control=Error(ex),
                environment=active_task.environment,
                kontinuation=kontinuation,
                status=TaskStatus.RUNNING,
            )
            new_state = state.with_task(active_task.task_id, new_task)
            return step(new_state)
    
    if isinstance(control, Value) and kontinuation:
        frame = kontinuation[0]
        rest_kontinuation = kontinuation[1:]
        
        value, frame_result = frame.on_value(control.v)
        
        if isinstance(frame_result, PopFrame):
            new_task = TaskState(
                task_id=active_task.task_id,
                control=Value(value),
                environment=active_task.environment,
                kontinuation=rest_kontinuation,
                status=TaskStatus.RUNNING,
            )
            new_state = state.with_task(active_task.task_id, new_task)
            return step(new_state)
        elif isinstance(frame_result, Continue):
            from doeff.cesk.frames_new import ReturnFrame
            if isinstance(frame, ReturnFrame):
                try:
                    item = frame.generator.send(control.v)
                    
                    if isinstance(item, EffectBase):
                        new_control = EffectControl(item)
                    else:
                        from doeff.program import ProgramBase
                        if isinstance(item, ProgramBase):
                            new_control = ProgramControl(item)
                        else:
                            raise TypeError(f"Program yielded unexpected type: {type(item).__name__}")
                    
                    new_task = TaskState(
                        task_id=active_task.task_id,
                        control=new_control,
                        environment=active_task.environment,
                        kontinuation=kontinuation,
                        status=TaskStatus.RUNNING,
                    )
                    new_state = state.with_task(active_task.task_id, new_task)
                    return step(new_state)
                except StopIteration as e:
                    new_task = TaskState(
                        task_id=active_task.task_id,
                        control=Value(e.value),
                        environment=active_task.environment,
                        kontinuation=rest_kontinuation,
                        status=TaskStatus.RUNNING,
                    )
                    new_state = state.with_task(active_task.task_id, new_task)
                    return step(new_state)
                except Exception as ex:
                    new_task = TaskState(
                        task_id=active_task.task_id,
                        control=Error(ex),
                        environment=active_task.environment,
                        kontinuation=kontinuation,
                        status=TaskStatus.RUNNING,
                    )
                    new_state = state.with_task(active_task.task_id, new_task)
                    return step(new_state)
    
    if isinstance(control, Error) and kontinuation:
        frame = kontinuation[0]
        rest_kontinuation = kontinuation[1:]
        
        error, frame_result = frame.on_error(control.ex)
        
        if isinstance(frame_result, PopFrame):
            new_control = Value(error) if not isinstance(error, BaseException) else Error(error)
            new_task = TaskState(
                task_id=active_task.task_id,
                control=new_control,
                environment=active_task.environment,
                kontinuation=rest_kontinuation,
                status=TaskStatus.RUNNING,
            )
            new_state = state.with_task(active_task.task_id, new_task)
            return step(new_state)
    
    return Failed(RuntimeError(f"Unhandled state: control={type(control).__name__}, K len={len(kontinuation)}"), state.store)


__all__ = [
    "step",
    "StepResult",
    "Done",
    "Failed",
    "Suspended",
    "NeedsAction",
]
