"""Core effect handlers: Ask, Get, Put, Modify, Tell, Pure."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff._vendor import FrozenDict
from doeff.cesk.state import ReadyStatus, TaskState, ValueControl

if TYPE_CHECKING:
    from doeff.cesk.frames import Kontinuation
    from doeff.cesk.types import Environment, Store


def handle_pure(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    return TaskState(
        control=ValueControl(effect.value),
        env=env,
        store=store,
        kontinuation=k,
        status=ReadyStatus(None),
    )


def handle_ask(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    value = env.get(effect.key)
    return TaskState(
        control=ValueControl(value),
        env=env,
        store=store,
        kontinuation=k,
        status=ReadyStatus(None),
    )


def handle_get(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    value = store.get(effect.key)
    return TaskState(
        control=ValueControl(value),
        env=env,
        store=store,
        kontinuation=k,
        status=ReadyStatus(None),
    )


def handle_put(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    new_store = {**store, effect.key: effect.value}
    return TaskState(
        control=ValueControl(None),
        env=env,
        store=new_store,
        kontinuation=k,
        status=ReadyStatus(None),
    )


def handle_modify(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    old_value = store.get(effect.key)
    new_value = effect.func(old_value)
    new_store = {**store, effect.key: new_value}
    return TaskState(
        control=ValueControl(old_value),
        env=env,
        store=new_store,
        kontinuation=k,
        status=ReadyStatus(None),
    )


def handle_tell(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    log = store.get("__log__", [])
    # Access __dict__ directly to avoid ProgramBase.__getattr__ infinite recursion
    effect_dict = object.__getattribute__(effect, "__dict__")
    messages = effect_dict.get("messages") or [effect_dict["message"]]
    new_log = log + list(messages)
    new_store = {**store, "__log__": new_log}
    return TaskState(
        control=ValueControl(None),
        env=env,
        store=new_store,
        kontinuation=k,
        status=ReadyStatus(None),
    )


__all__ = [
    "handle_ask",
    "handle_get",
    "handle_modify",
    "handle_pure",
    "handle_put",
    "handle_tell",
]
