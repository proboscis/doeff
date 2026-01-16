"""The CESK machine step function for unified multi-task architecture."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from doeff._vendor import FrozenDict
from doeff._types_internal import EffectBase
from doeff.cesk.types import Store, TaskId
from doeff.cesk.frames import (
    Frame,
    FrameResult,
    GatherFrame,
    InterceptFrame,
    ReturnFrame,
)
from doeff.cesk.state import (
    Control,
    EffectControl,
    Error,
    ProgramControl,
    Value,
)
from doeff.cesk.unified_state import (
    UnifiedCESKState as CESKState,
    TaskState,
    TaskStatus,
)
from doeff.cesk.events import (
    AllTasksComplete,
    EffectSuspended,
    Event,
    Stepped,
    TaskBlocked,
    TaskCompleted,
    TaskFailed,
)
from doeff.cesk.actions import Action, Resume, RunProgram

if TYPE_CHECKING:
    from doeff.program import Program


class UnhandledEffectError(Exception):
    pass


class InterpreterInvariantError(Exception):
    pass


Handler = Callable[[EffectBase, "HandlerContext"], tuple[Action, ...]]


class HandlerContext:
    def __init__(
        self,
        env: FrozenDict[Any, Any],
        store: Store,
        task_id: TaskId,
        kontinuation: list[Frame],
    ):
        self.env = env
        self.store = store
        self.task_id = task_id
        self.kontinuation = kontinuation


def _to_generator(program: Program) -> Any:
    from doeff.program import KleisliProgramCall, ProgramBase
    
    if isinstance(program, KleisliProgramCall):
        return program.to_generator()
    
    if isinstance(program, ProgramBase):
        to_gen = getattr(program, "to_generator", None)
        if callable(to_gen):
            return to_gen()
    
    raise InterpreterInvariantError(f"Cannot convert {type(program).__name__} to generator")


def _step_program_control(
    task: TaskState,
    state: CESKState,
) -> tuple[TaskState, CESKState]:
    control = task.control
    if not isinstance(control, ProgramControl):
        raise InterpreterInvariantError("Expected ProgramControl")
    program = control.program
    
    from doeff.effects.pure import PureEffect
    if isinstance(program, PureEffect):
        new_task = task.with_control(Value(program.value))
        return new_task, state
    
    if isinstance(program, EffectBase):
        new_task = task.with_control(EffectControl(program))
        return new_task, state
    
    try:
        gen = _to_generator(program)
        from doeff.program import KleisliProgramCall
        program_call = program if isinstance(program, KleisliProgramCall) else None
        
        item = next(gen)
        
        if isinstance(item, EffectBase):
            new_control = EffectControl(item)
        elif hasattr(item, "to_generator"):
            new_control = ProgramControl(item)
        else:
            err = InterpreterInvariantError(
                f"Program yielded unexpected type: {type(item).__name__}"
            )
            new_control = Error(err)
            new_task = task.with_control(new_control)
            return new_task, state
        
        frame = ReturnFrame(gen, task.env, program_call)
        new_task = task.with_control(new_control).push_frame(frame)
        return new_task, state
        
    except StopIteration as e:
        new_task = task.with_control(Value(e.value))
        return new_task, state
    except Exception as ex:
        new_task = task.with_control(Error(ex))
        return new_task, state


def _step_value_with_frame(
    task: TaskState,
    state: CESKState,
    value: Any,
    frame: Frame,
) -> tuple[TaskState, CESKState]:
    result = frame.on_value(value, task.env)
    return _apply_frame_result(task, state, result, frame)


def _step_error_with_frame(
    task: TaskState,
    state: CESKState,
    error: BaseException,
    frame: Frame,
) -> tuple[TaskState, CESKState]:
    result = frame.on_error(error, task.env)
    return _apply_frame_result(task, state, result, frame)


def _apply_frame_result(
    task: TaskState,
    state: CESKState,
    result: FrameResult,
    original_frame: Frame,
) -> tuple[TaskState, CESKState]:
    if result.keep_frame:
        new_task = TaskState(
            task_id=task.task_id,
            control=result.control,
            env=result.env,
            kontinuation=task.kontinuation,
            status=task.status,
            condition=task.condition,
            parent_id=task.parent_id,
            spawn_id=task.spawn_id,
        )
    else:
        _, popped_task = task.pop_frame()
        new_task = TaskState(
            task_id=popped_task.task_id,
            control=result.control,
            env=result.env,
            kontinuation=popped_task.kontinuation,
            status=popped_task.status,
            condition=popped_task.condition,
            parent_id=popped_task.parent_id,
            spawn_id=popped_task.spawn_id,
        )
    
    new_state = state
    for action in result.actions:
        if action[0] == "push_gather_frame":
            _, remaining, collected, saved_env = action
            gather_frame = GatherFrame(remaining, collected, saved_env)
            new_task = new_task.push_frame(gather_frame)
        elif action[0] == "capture_log":
            log_start = action[1]
            current_log = new_state.store.get("__log__", [])
            captured = current_log[log_start:]
            from doeff._types_internal import ListenResult
            from doeff.utils import BoundedLog
            if isinstance(new_task.control, Value):
                inner_value = new_task.control.v.value if hasattr(new_task.control.v, "value") else new_task.control.v
                listen_result = ListenResult(value=inner_value, log=BoundedLog(captured))
                new_task = new_task.with_control(Value(listen_result))
    
    return new_task, new_state


def _has_intercept_frame(kontinuation: list[Frame]) -> bool:
    return any(isinstance(f, InterceptFrame) for f in kontinuation)


def _apply_intercept_transforms(
    kontinuation: list[Frame],
    effect: EffectBase,
) -> EffectBase | Any:
    for frame in kontinuation:
        if isinstance(frame, InterceptFrame):
            for transform in frame.transforms:
                result = transform(effect)
                if result is not None:
                    return result
    return effect


def unified_step(
    state: CESKState,
    handlers: dict[type, Handler] | None = None,
) -> Event:
    runnable = state.runnable_tasks()
    
    if not runnable:
        if state.is_complete():
            return AllTasksComplete(state=state)
        blocked = state.blocked_tasks()
        if blocked:
            task_id, _ = blocked[0]
            return TaskBlocked(task_id=task_id, state=state)
        return AllTasksComplete(state=state)
    
    task_id = runnable[0]
    task = state.get_task(task_id)
    if task is None:
        return AllTasksComplete(state=state)
    
    control = task.control
    
    if isinstance(control, ProgramControl):
        new_task, new_state = _step_program_control(task, state)
        new_state = new_state.update_task(new_task)
        return Stepped(state=new_state)
    
    if isinstance(control, EffectControl):
        effect = control.effect
        
        from doeff.effects.pure import PureEffect
        if isinstance(effect, PureEffect):
            new_task = task.with_control(Value(effect.value))
            new_state = state.update_task(new_task)
            return Stepped(state=new_state)
        
        if _has_intercept_frame(task.kontinuation):
            transformed = _apply_intercept_transforms(task.kontinuation, effect)
            if transformed is not effect:
                if isinstance(transformed, EffectBase):
                    new_task = task.with_control(EffectControl(transformed))
                    new_state = state.update_task(new_task)
                    return Stepped(state=new_state)
                elif hasattr(transformed, "_factory") or hasattr(transformed, "__iter__"):
                    new_task = task.with_control(ProgramControl(transformed))
                    new_state = state.update_task(new_task)
                    return Stepped(state=new_state)
        
        if handlers and type(effect) in handlers:
            handler = handlers[type(effect)]
            ctx = HandlerContext(
                env=task.env,
                store=state.store,
                task_id=task_id,
                kontinuation=task.kontinuation,
            )
            actions = handler(effect, ctx)
            
            if actions and isinstance(actions[0], Resume):
                resume = actions[0]
                new_store = resume.store if resume.store is not None else state.store
                new_task = task.with_control(Value(resume.value))
                new_state = state.with_store(new_store).update_task(new_task)
                return Stepped(state=new_state)
            
            if actions and isinstance(actions[0], RunProgram):
                run_action = actions[0]
                sub_program = run_action.program
                new_env = run_action.env if run_action.env is not None else task.env
                
                from doeff.cesk.frames import LocalFrame, SafeFrame
                from doeff.effects import LocalEffect, ResultSafeEffect
                
                if isinstance(effect, ResultSafeEffect):
                    frame = SafeFrame(task.env)
                    new_task = task.with_control(ProgramControl(sub_program)).with_env(new_env).push_frame(frame)
                elif isinstance(effect, LocalEffect):
                    frame = LocalFrame(task.env)
                    new_task = task.with_control(ProgramControl(sub_program)).with_env(new_env).push_frame(frame)
                else:
                    new_task = task.with_control(ProgramControl(sub_program)).with_env(new_env)
                
                new_state = state.update_task(new_task)
                return Stepped(state=new_state)
        
        return EffectSuspended(
            task_id=task_id,
            effect=effect,
            state=state,
        )
    
    if isinstance(control, Value):
        if not task.kontinuation:
            new_state = state.complete_task(task_id, control.v)
            if task_id == state.main_task_id:
                return TaskCompleted(
                    task_id=task_id,
                    value=control.v,
                    state=new_state,
                )
            return Stepped(state=new_state)
        
        frame = task.kontinuation[0]
        new_task, new_state = _step_value_with_frame(task, state, control.v, frame)
        new_state = new_state.update_task(new_task)
        return Stepped(state=new_state)
    
    if isinstance(control, Error):
        if not task.kontinuation:
            new_state = state.fail_task(task_id, control.ex)
            if task_id == state.main_task_id:
                return TaskFailed(
                    task_id=task_id,
                    error=control.ex,
                    state=new_state,
                )
            return Stepped(state=new_state)
        
        frame = task.kontinuation[0]
        new_task, new_state = _step_error_with_frame(task, state, control.ex, frame)
        new_state = new_state.update_task(new_task)
        return Stepped(state=new_state)
    
    raise InterpreterInvariantError(f"Unknown control type: {type(control)}")


__all__ = [
    "unified_step",
    "Handler",
    "HandlerContext",
    "UnhandledEffectError",
    "InterpreterInvariantError",
]
