from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.cesk.handlers import HandlerContext, PerformAction, ResumeWith, register_handler
from doeff.cesk.actions import CreateTask, CreateTasks
from doeff.cesk.types import TaskId

if TYPE_CHECKING:
    from doeff.effects import SpawnEffect, TaskJoinEffect, GatherEffect, RaceEffect


@register_handler(type(None))
def handle_spawn(effect: SpawnEffect, ctx: HandlerContext) -> PerformAction:
    from doeff.effects import SpawnEffect
    
    if not isinstance(effect, SpawnEffect):
        raise TypeError(f"Expected SpawnEffect, got {type(effect)}")
    
    task_id_counter = ctx.store.get("__task_id_counter__", 0)
    new_task_id = TaskId(task_id_counter)
    ctx.store["__task_id_counter__"] = task_id_counter + 1
    
    current_task_id = ctx.store.get("__current_task_id__")
    
    return PerformAction(CreateTask(
        task_id=new_task_id,
        program=effect.program,
        env=ctx.environment,
        parent_task_id=current_task_id,
    ))


@register_handler(type(None))
def handle_task_join(effect: TaskJoinEffect, ctx: HandlerContext) -> ResumeWith:
    from doeff.effects import TaskJoinEffect
    
    if not isinstance(effect, TaskJoinEffect):
        raise TypeError(f"Expected TaskJoinEffect, got {type(effect)}")
    
    task_results = ctx.store.get("__task_results__", {})
    task_id = effect.task._handle if hasattr(effect.task, '_handle') else None
    
    if task_id in task_results:
        return ResumeWith(task_results[task_id])
    
    raise RuntimeError(f"Task {task_id} not completed")


@register_handler(type(None))
def handle_gather(effect: GatherEffect, ctx: HandlerContext) -> PerformAction:
    from doeff.effects import GatherEffect
    
    if not isinstance(effect, GatherEffect):
        raise TypeError(f"Expected GatherEffect, got {type(effect)}")
    
    task_id_counter = ctx.store.get("__task_id_counter__", 0)
    task_specs = []
    
    for prog in effect.programs:
        task_id = TaskId(task_id_counter)
        task_id_counter += 1
        task_specs.append((task_id, prog, ctx.environment))
    
    ctx.store["__task_id_counter__"] = task_id_counter
    
    current_task_id = ctx.store.get("__current_task_id__")
    
    return PerformAction(CreateTasks(task_specs, current_task_id))


@register_handler(type(None))
def handle_race(effect: RaceEffect, ctx: HandlerContext) -> PerformAction:
    from doeff.effects import RaceEffect
    
    if not isinstance(effect, RaceEffect):
        raise TypeError(f"Expected RaceEffect, got {type(effect)}")
    
    task_id_counter = ctx.store.get("__task_id_counter__", 0)
    task_specs = []
    
    for prog in effect.programs:
        task_id = TaskId(task_id_counter)
        task_id_counter += 1
        task_specs.append((task_id, prog, ctx.environment))
    
    ctx.store["__task_id_counter__"] = task_id_counter
    
    current_task_id = ctx.store.get("__current_task_id__")
    
    return PerformAction(CreateTasks(task_specs, current_task_id))


__all__ = [
    "handle_spawn",
    "handle_task_join",
    "handle_gather",
    "handle_race",
]
