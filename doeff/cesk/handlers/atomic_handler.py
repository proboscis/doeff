"""Atomic effect handler for AtomicGet and AtomicUpdate effects."""

from __future__ import annotations

from doeff.do import do
from doeff._types_internal import EffectBase
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.state import CESKState
from doeff.effects.atomic import AtomicGetEffect, AtomicUpdateEffect


@do
def atomic_handler(effect: EffectBase, ctx: HandlerContext):
    store = dict(ctx.store)

    if isinstance(effect, AtomicGetEffect):
        key = effect.key
        if key in store:
            value = store[key]
        elif effect.default_factory is not None:
            value = effect.default_factory()
            store = {**store, key: value}
        else:
            value = None
        return CESKState.with_value(value, ctx.env, store, ctx.k)

    if isinstance(effect, AtomicUpdateEffect):
        key = effect.key
        if key in store:
            old_value = store[key]
        elif effect.default_factory is not None:
            old_value = effect.default_factory()
        else:
            old_value = None
        new_value = effect.updater(old_value)
        new_store = {**store, key: new_value}
        return CESKState.with_value(new_value, ctx.env, new_store, ctx.k)

    result = yield effect
    return result


__all__ = ["atomic_handler"]
