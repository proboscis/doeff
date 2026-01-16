from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.handlers import HandlerContext, ResumeWith, register_handler

if TYPE_CHECKING:
    from doeff.effects import AskEffect, GetEffect, PutEffect, ModifyEffect, TellEffect


@register_handler(type(None))
def handle_ask(effect: AskEffect, ctx: HandlerContext) -> ResumeWith:
    from doeff.effects import AskEffect
    
    if not isinstance(effect, AskEffect):
        raise TypeError(f"Expected AskEffect, got {type(effect)}")
    
    key = effect.key
    if key in ctx.environment:
        return ResumeWith(ctx.environment[key])
    
    raise KeyError(f"Key '{key}' not found in environment")


@register_handler(type(None))
def handle_get(effect: GetEffect, ctx: HandlerContext) -> ResumeWith:
    from doeff.effects import GetEffect
    
    if not isinstance(effect, GetEffect):
        raise TypeError(f"Expected GetEffect, got {type(effect)}")
    
    key = effect.key
    if key in ctx.store:
        return ResumeWith(ctx.store[key])
    
    default = getattr(effect, 'default', None)
    if default is not None:
        return ResumeWith(default)
    
    raise KeyError(f"Key '{key}' not found in store")


@register_handler(type(None))
def handle_put(effect: PutEffect, ctx: HandlerContext) -> ResumeWith:
    from doeff.effects import PutEffect
    
    if not isinstance(effect, PutEffect):
        raise TypeError(f"Expected PutEffect, got {type(effect)}")
    
    ctx.store[effect.key] = effect.value
    return ResumeWith(None)


@register_handler(type(None))
def handle_modify(effect: ModifyEffect, ctx: HandlerContext) -> ResumeWith:
    from doeff.effects import ModifyEffect
    
    if not isinstance(effect, ModifyEffect):
        raise TypeError(f"Expected ModifyEffect, got {type(effect)}")
    
    key = effect.key
    if key not in ctx.store:
        raise KeyError(f"Key '{key}' not found in store")
    
    old_value = ctx.store[key]
    new_value = effect.transform(old_value)
    ctx.store[key] = new_value
    return ResumeWith(new_value)


@register_handler(type(None))
def handle_tell(effect: TellEffect, ctx: HandlerContext) -> ResumeWith:
    from doeff.effects import TellEffect
    
    if not isinstance(effect, TellEffect):
        raise TypeError(f"Expected TellEffect, got {type(effect)}")
    
    log_key = "__log__"
    if log_key not in ctx.store:
        ctx.store[log_key] = []
    
    messages = effect.messages if isinstance(effect.messages, list) else [effect.messages]
    ctx.store[log_key].extend(messages)
    
    return ResumeWith(None)


__all__ = [
    "handle_ask",
    "handle_get",
    "handle_put",
    "handle_modify",
    "handle_tell",
]
