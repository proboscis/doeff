from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.cesk.handlers import HandlerContext, PerformAction, register_handler
from doeff.cesk.actions import RunProgram

if TYPE_CHECKING:
    from doeff.effects import LocalEffect, ResultSafeEffect, WriterListenEffect, InterceptEffect


@register_handler(type(None))
def handle_local(effect: LocalEffect, ctx: HandlerContext) -> PerformAction:
    from doeff.effects import LocalEffect
    from doeff._vendor import FrozenDict
    
    if not isinstance(effect, LocalEffect):
        raise TypeError(f"Expected LocalEffect, got {type(effect)}")
    
    new_env = ctx.environment | FrozenDict(effect.env_update)
    return PerformAction(RunProgram(effect.sub_program, new_env))


@register_handler(type(None))
def handle_safe(effect: ResultSafeEffect, ctx: HandlerContext) -> PerformAction:
    from doeff.effects import ResultSafeEffect
    
    if not isinstance(effect, ResultSafeEffect):
        raise TypeError(f"Expected ResultSafeEffect, got {type(effect)}")
    
    return PerformAction(RunProgram(effect.sub_program, ctx.environment))


@register_handler(type(None))
def handle_listen(effect: WriterListenEffect, ctx: HandlerContext) -> PerformAction:
    from doeff.effects import WriterListenEffect
    
    if not isinstance(effect, WriterListenEffect):
        raise TypeError(f"Expected WriterListenEffect, got {type(effect)}")
    
    log_key = "__log__"
    if log_key not in ctx.store:
        ctx.store[log_key] = []
    
    return PerformAction(RunProgram(effect.sub_program, ctx.environment))


@register_handler(type(None))
def handle_intercept(effect: InterceptEffect, ctx: HandlerContext) -> PerformAction:
    from doeff.effects import InterceptEffect
    
    if not isinstance(effect, InterceptEffect):
        raise TypeError(f"Expected InterceptEffect, got {type(effect)}")
    
    return PerformAction(RunProgram(effect.program, ctx.environment))


__all__ = [
    "handle_local",
    "handle_safe",
    "handle_listen",
    "handle_intercept",
]
