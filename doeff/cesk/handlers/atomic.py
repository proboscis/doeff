"""Atomic shared-state effect handlers."""

from __future__ import annotations

from doeff.cesk.frames import ContinueValue, FrameResult
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store
from doeff.effects.atomic import AtomicGetEffect, AtomicUpdateEffect


def handle_atomic_get(
    effect: AtomicGetEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    key = effect.key
    if key in store:
        value = store[key]
    elif effect.default_factory is not None:
        value = effect.default_factory()
        store = {**store, key: value}
    else:
        value = None
    return ContinueValue(
        value=value,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


def handle_atomic_update(
    effect: AtomicUpdateEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    key = effect.key
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
        env=task_state.env,
        store=new_store,
        k=task_state.kontinuation,
    )


__all__ = [
    "handle_atomic_get",
    "handle_atomic_update",
]
