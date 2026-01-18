"""Core effect handlers for the unified CESK architecture."""

from __future__ import annotations

from doeff.cesk.errors import MissingEnvKeyError
from doeff.cesk.frames import ContinueValue, FrameResult
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store
from doeff.effects.pure import PureEffect
from doeff.effects.reader import AskEffect
from doeff.effects.state import StateGetEffect, StatePutEffect, StateModifyEffect


def handle_pure(
    effect: PureEffect,
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
    effect: AskEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    key = effect.key
    if key not in task_state.env:
        raise MissingEnvKeyError(key)
    value = task_state.env[key]
    return ContinueValue(
        value=value,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


def handle_state_get(
    effect: StateGetEffect,
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


def handle_state_put(
    effect: StatePutEffect,
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


def handle_state_modify(
    effect: StateModifyEffect,
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
    "handle_state_get",
    "handle_state_put",
    "handle_state_modify",
]
