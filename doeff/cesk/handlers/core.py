"""Core effect handlers for Reader, State, and Writer effects."""

from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.cesk.actions import AppendLog, Resume

if TYPE_CHECKING:
    from doeff.cesk.step import HandlerContext
    from doeff.effects import (
        AskEffect,
        StateGetEffect,
        StateModifyEffect,
        StatePutEffect,
        WriterTellEffect,
    )


def handle_ask(effect: AskEffect, ctx: HandlerContext) -> tuple[Resume, ...]:
    value = ctx.env.get(effect.key, None)
    return (Resume(value),)


def handle_get(effect: StateGetEffect, ctx: HandlerContext) -> tuple[Resume, ...]:
    value = ctx.store.get(effect.key, None)
    return (Resume(value),)


def handle_put(effect: StatePutEffect, ctx: HandlerContext) -> tuple[Resume, ...]:
    new_store = {**ctx.store, effect.key: effect.value}
    return (Resume(None, new_store),)


def handle_modify(effect: StateModifyEffect, ctx: HandlerContext) -> tuple[Resume, ...]:
    current = ctx.store.get(effect.key, None)
    new_value = effect.func(current)
    new_store = {**ctx.store, effect.key: new_value}
    return (Resume(new_value, new_store),)


def handle_tell(effect: WriterTellEffect, ctx: HandlerContext) -> tuple[Resume, ...]:
    current_log = ctx.store.get("__log__", [])
    if isinstance(effect.message, list):
        new_log = current_log + effect.message
    else:
        new_log = current_log + [effect.message]
    new_store = {**ctx.store, "__log__": new_log}
    return (Resume(None, new_store),)


__all__ = [
    "handle_ask",
    "handle_get",
    "handle_put",
    "handle_modify",
    "handle_tell",
]
