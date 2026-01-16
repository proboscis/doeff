"""Core effect handlers.

Handles the most basic effects:
- AskEffect: Read from environment
- StateGetEffect: Read from store
- StatePutEffect: Replace store value
- StateModifyEffect: Update store value
- WriterTellEffect: Append to log
- PureEffect: Return a pure value
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.actions import Resume, ResumeWithStore
from doeff.cesk.handlers import HandlerContext, HandlerResult

if TYPE_CHECKING:
    from doeff.effects import (
        AskEffect,
        PureEffect,
        StateGetEffect,
        StateModifyEffect,
        StatePutEffect,
        WriterTellEffect,
    )


def handle_ask(effect: AskEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle AskEffect: read value from environment.

    Returns the value associated with effect.key in the environment,
    or None if key is not found.
    """
    value = ctx.env.get(effect.key)
    return HandlerResult.resume(value)


def handle_get(effect: StateGetEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle StateGetEffect: read value from store.

    Returns the value associated with effect.key in the store,
    or None if key is not found.
    """
    value = ctx.store.get(effect.key)
    return HandlerResult.resume(value)


def handle_put(effect: StatePutEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle StatePutEffect: replace value in store.

    Sets store[effect.key] = effect.value and resumes with the old value.
    """
    old_value = ctx.store.get(effect.key)
    new_store = dict(ctx.store)
    new_store[effect.key] = effect.value
    return HandlerResult.resume_with_store(old_value, new_store)


def handle_modify(effect: StateModifyEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle StateModifyEffect: update value in store with function.

    Applies effect.func to the current value and stores the result.
    Resumes with the old value.
    """
    old_value = ctx.store.get(effect.key)
    new_value = effect.func(old_value)
    new_store = dict(ctx.store)
    new_store[effect.key] = new_value
    return HandlerResult.resume_with_store(old_value, new_store)


def handle_tell(effect: WriterTellEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle WriterTellEffect: append to log.

    Appends effect.message to the __log__ list in the store.
    Resumes with None.
    """
    new_store = dict(ctx.store)
    log = list(new_store.get("__log__", []))
    log.append(effect.message)
    new_store["__log__"] = log
    return HandlerResult.resume_with_store(None, new_store)


def handle_pure(effect: PureEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle PureEffect: return a pure value.

    Simply resumes with the effect's value.
    """
    return HandlerResult.resume(effect.value)


__all__ = [
    "handle_ask",
    "handle_get",
    "handle_put",
    "handle_modify",
    "handle_tell",
    "handle_pure",
]
