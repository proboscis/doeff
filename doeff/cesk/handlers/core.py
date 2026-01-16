"""Core effect handlers for the unified CESK architecture.

Handlers: handle_pure, handle_ask, handle_get, handle_put, handle_modify
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.frames import ContinueValue

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk.state import TaskState
    from doeff.cesk.types import Store
    from doeff.cesk.frames import FrameResult


def handle_pure(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    return ContinueValue(
        value=effect.value,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


def handle_ask(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    key = effect.key
    if key not in task_state.env:
        raise KeyError(f"Missing environment key: {key!r}")
    value = task_state.env[key]
    return ContinueValue(
        value=value,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


def handle_get(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    value = store.get(effect.key)
    return ContinueValue(
        value=value,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


def handle_put(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    new_store = {**store, effect.key: effect.value}
    return ContinueValue(
        value=None,
        env=task_state.env,
        store=new_store,
        k=task_state.kontinuation,
    )


def handle_modify(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    old_value = store.get(effect.key)
    new_value = effect.func(old_value)
    new_store = {**store, effect.key: new_value}
    return ContinueValue(
        value=new_value,
        env=task_state.env,
        store=new_store,
        k=task_state.kontinuation,
    )


__all__ = [
    "handle_pure",
    "handle_ask",
    "handle_get",
    "handle_put",
    "handle_modify",
]
