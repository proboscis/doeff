"""Atomic shared-state effect handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.cesk.frames import ContinueValue, FrameResult
from doeff.effects.atomic import AtomicGetEffect, AtomicUpdateEffect

if TYPE_CHECKING:
    from doeff.cesk.runtime.context import HandlerContext


def handle_atomic_get(
    effect: AtomicGetEffect,
    ctx: HandlerContext,
) -> FrameResult:
    key = effect.key
    store = ctx.store
    if key in store:
        value = store[key]
    elif effect.default_factory is not None:
        value = effect.default_factory()
        store = {**store, key: value}
    else:
        value = None
    return ContinueValue(
        value=value,
        env=ctx.task_state.env,
        store=store,
        k=ctx.task_state.kontinuation,
    )


def handle_atomic_update(
    effect: AtomicUpdateEffect,
    ctx: HandlerContext,
) -> FrameResult:
    key = effect.key
    store = ctx.store
    if key in store:
        old_value = store[key]
    elif effect.default_factory is not None:
        old_value = effect.default_factory()
    else:
        old_value = None
    new_value = effect.updater(old_value)
    new_store = {**store, key: new_value}
    return ContinueValue(
        value=new_value,
        env=ctx.task_state.env,
        store=new_store,
        k=ctx.task_state.kontinuation,
    )


__all__ = [
    "handle_atomic_get",
    "handle_atomic_update",
]
