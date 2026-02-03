"""State effect handler for Get, Put, and Modify effects."""

from __future__ import annotations

from doeff.do import do
from doeff._types_internal import EffectBase
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.state import CESKState
from doeff.effects.state import StateGetEffect, StateModifyEffect, StatePutEffect


@do
def state_handler(effect: EffectBase, ctx: HandlerContext):
    store = dict(ctx.store)

    if isinstance(effect, StateGetEffect):
        key = effect.key
        if key in store:
            return CESKState.with_value(store[key], ctx.env, store, ctx.k)
        return CESKState.with_error(KeyError(key), ctx.env, store, ctx.k)

    if isinstance(effect, StatePutEffect):
        new_store = {**store, effect.key: effect.value}
        return CESKState.with_value(None, ctx.env, new_store, ctx.k)

    if isinstance(effect, StateModifyEffect):
        key = effect.key
        old_value = store.get(key, None)
        try:
            new_value = effect.func(old_value)
        except Exception as ex:
            return CESKState.with_error(ex, ctx.env, store, ctx.k)
        new_store = {**store, key: new_value}
        return CESKState.with_value(new_value, ctx.env, new_store, ctx.k)

    result = yield effect
    return result


__all__ = ["state_handler"]
