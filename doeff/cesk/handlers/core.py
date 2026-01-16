from __future__ import annotations

from typing import Any

from doeff.cesk.handlers import HandlerContext, ResumeWith, register_handler
from doeff.effects.pure import PureEffect
from doeff.effects.reader import AskEffect
from doeff.effects.state import StateGetEffect, StatePutEffect, StateModifyEffect
from doeff.effects.writer import WriterTellEffect


@register_handler(PureEffect)
def handle_pure(effect: Any, ctx: HandlerContext) -> ResumeWith:
    if not isinstance(effect, PureEffect):
        raise TypeError(f"Expected PureEffect, got {type(effect)}")
    return ResumeWith(effect.value)


@register_handler(AskEffect)
def handle_ask(effect: Any, ctx: HandlerContext) -> ResumeWith:
    if not isinstance(effect, AskEffect):
        raise TypeError(f"Expected AskEffect, got {type(effect)}")
    key = effect.key
    if key in ctx.environment:
        return ResumeWith(ctx.environment[key])
    raise KeyError(f"Key '{key}' not found in environment")


@register_handler(StateGetEffect)
def handle_state_get(effect: Any, ctx: HandlerContext) -> ResumeWith:
    if not isinstance(effect, StateGetEffect):
        raise TypeError(f"Expected StateGetEffect, got {type(effect)}")
    key = effect.key
    if key in ctx.store:
        return ResumeWith(ctx.store[key])
    raise KeyError(f"Key '{key}' not found in store")


@register_handler(StatePutEffect)
def handle_state_put(effect: Any, ctx: HandlerContext) -> ResumeWith:
    if not isinstance(effect, StatePutEffect):
        raise TypeError(f"Expected StatePutEffect, got {type(effect)}")
    ctx.store[effect.key] = effect.value
    return ResumeWith(None)


@register_handler(StateModifyEffect)
def handle_state_modify(effect: Any, ctx: HandlerContext) -> ResumeWith:
    if not isinstance(effect, StateModifyEffect):
        raise TypeError(f"Expected StateModifyEffect, got {type(effect)}")
    key = effect.key
    if key not in ctx.store:
        raise KeyError(f"Key '{key}' not found in store")
    old_value = ctx.store[key]
    new_value = effect.func(old_value)
    ctx.store[key] = new_value
    return ResumeWith(new_value)


@register_handler(WriterTellEffect)
def handle_tell(effect: Any, ctx: HandlerContext) -> ResumeWith:
    if not isinstance(effect, WriterTellEffect):
        raise TypeError(f"Expected WriterTellEffect, got {type(effect)}")
    log_key = "__log__"
    if log_key not in ctx.store:
        ctx.store[log_key] = []
    messages = effect.messages if isinstance(effect.messages, list) else [effect.messages]
    ctx.store[log_key].extend(messages)
    return ResumeWith(None)


__all__ = [
    "handle_pure",
    "handle_ask",
    "handle_state_get",
    "handle_state_put",
    "handle_state_modify",
    "handle_tell",
]
