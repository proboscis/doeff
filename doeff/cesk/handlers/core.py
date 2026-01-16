"""Core effect handlers: Pure, Ask, Get, Put, Modify."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff._vendor import FrozenDict
from doeff.cesk.frames import ContinueValue

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk.frames import FrameResult
    from doeff.cesk.state import TaskState
    from doeff.cesk.types import Store


def handle_pure(effect: EffectBase, task: TaskState, store: Store) -> FrameResult:
    from doeff.effects.pure import PureEffect

    if not isinstance(effect, PureEffect):
        raise TypeError(f"Expected PureEffect, got {type(effect).__name__}")

    return ContinueValue(
        value=effect.value,
        env=task.env,
        store=store,
        k=task.kontinuation,
    )


def handle_ask(effect: EffectBase, task: TaskState, store: Store) -> FrameResult:
    from doeff.effects.reader import AskEffect

    if not isinstance(effect, AskEffect):
        raise TypeError(f"Expected AskEffect, got {type(effect).__name__}")

    try:
        value = task.env[effect.key]
    except KeyError as e:
        raise KeyError(f"Environment key not found: {effect.key!r}") from e

    return ContinueValue(
        value=value,
        env=task.env,
        store=store,
        k=task.kontinuation,
    )


def handle_get(effect: EffectBase, task: TaskState, store: Store) -> FrameResult:
    from doeff.effects.state import StateGetEffect

    if not isinstance(effect, StateGetEffect):
        raise TypeError(f"Expected StateGetEffect, got {type(effect).__name__}")

    try:
        value = store[effect.key]
    except KeyError as e:
        raise KeyError(f"State key not found: {effect.key!r}") from e

    return ContinueValue(
        value=value,
        env=task.env,
        store=store,
        k=task.kontinuation,
    )


def handle_put(effect: EffectBase, task: TaskState, store: Store) -> FrameResult:
    from doeff.effects.state import StatePutEffect

    if not isinstance(effect, StatePutEffect):
        raise TypeError(f"Expected StatePutEffect, got {type(effect).__name__}")

    store[effect.key] = effect.value

    return ContinueValue(
        value=None,
        env=task.env,
        store=store,
        k=task.kontinuation,
    )


def handle_modify(effect: EffectBase, task: TaskState, store: Store) -> FrameResult:
    from doeff.effects.state import StateModifyEffect

    if not isinstance(effect, StateModifyEffect):
        raise TypeError(f"Expected StateModifyEffect, got {type(effect).__name__}")

    try:
        current_value = store[effect.key]
    except KeyError as e:
        raise KeyError(f"State key not found: {effect.key!r}") from e

    new_value = effect.func(current_value)
    store[effect.key] = new_value

    return ContinueValue(
        value=new_value,
        env=task.env,
        store=store,
        k=task.kontinuation,
    )


__all__ = [
    "handle_pure",
    "handle_ask",
    "handle_get",
    "handle_put",
    "handle_modify",
]
