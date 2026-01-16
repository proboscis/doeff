from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from doeff.cesk.handlers import HandlerContext, ResumeWith, PerformAction, register_handler
from doeff.cesk.actions import ScheduleAt, GetCurrentTime

if TYPE_CHECKING:
    from doeff.effects import DelayEffect, WaitUntilEffect, GetTimeEffect


@register_handler(type(None))
def handle_delay(effect: DelayEffect, ctx: HandlerContext) -> PerformAction:
    from doeff.effects import DelayEffect
    
    if not isinstance(effect, DelayEffect):
        raise TypeError(f"Expected DelayEffect, got {type(effect)}")
    
    current_time = ctx.store.get("__current_time__", datetime.now())
    target_time = current_time + effect.duration
    
    from doeff.cesk.types import TaskId
    task_id = ctx.store.get("__current_task_id__", TaskId(0))
    
    return PerformAction(ScheduleAt(target_time, task_id))


@register_handler(type(None))
def handle_wait_until(effect: WaitUntilEffect, ctx: HandlerContext) -> PerformAction:
    from doeff.effects import WaitUntilEffect
    
    if not isinstance(effect, WaitUntilEffect):
        raise TypeError(f"Expected WaitUntilEffect, got {type(effect)}")
    
    from doeff.cesk.types import TaskId
    task_id = ctx.store.get("__current_task_id__", TaskId(0))
    
    return PerformAction(ScheduleAt(effect.target, task_id))


@register_handler(type(None))
def handle_get_time(effect: GetTimeEffect, ctx: HandlerContext) -> ResumeWith:
    from doeff.effects import GetTimeEffect
    
    if not isinstance(effect, GetTimeEffect):
        raise TypeError(f"Expected GetTimeEffect, got {type(effect)}")
    
    current_time = ctx.store.get("__current_time__", datetime.now())
    return ResumeWith(current_time)


__all__ = [
    "handle_delay",
    "handle_wait_until",
    "handle_get_time",
]
