"""IO effect handlers: IO, Await, Cache operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.state import (
    AwaitExternalRequest,
    PerformIORequest,
    ReadyStatus,
    RequestingStatus,
    TaskState,
    ValueControl,
)

if TYPE_CHECKING:
    from doeff.cesk.frames import Kontinuation
    from doeff.cesk.types import Environment, Store


def handle_io(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    return TaskState(
        control=ValueControl(None),
        env=env,
        store=store,
        kontinuation=k,
        status=RequestingStatus(PerformIORequest(effect.action)),
    )


def handle_await(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    return TaskState(
        control=ValueControl(None),
        env=env,
        store=store,
        kontinuation=k,
        status=RequestingStatus(AwaitExternalRequest(effect.awaitable)),
    )


def handle_cache_get(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    cache_storage = store.get("__cache_storage__", {})
    key = effect.key
    if key in cache_storage:
        value = cache_storage[key]
        return TaskState(
            control=ValueControl(value),
            env=env,
            store=store,
            kontinuation=k,
            status=ReadyStatus(None),
        )
    raise KeyError(key)


def handle_cache_put(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    cache_storage = store.get("__cache_storage__", {})
    new_cache = {**cache_storage, effect.key: effect.value}
    new_store = {**store, "__cache_storage__": new_cache}
    return TaskState(
        control=ValueControl(None),
        env=env,
        store=new_store,
        kontinuation=k,
        status=ReadyStatus(None),
    )


def handle_cache_exists(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    cache_storage = store.get("__cache_storage__", {})
    exists = effect.key in cache_storage
    return TaskState(
        control=ValueControl(exists),
        env=env,
        store=store,
        kontinuation=k,
        status=ReadyStatus(None),
    )


def handle_cache_delete(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    cache_storage = store.get("__cache_storage__", {})
    new_cache = {k: v for k, v in cache_storage.items() if k != effect.key}
    new_store = {**store, "__cache_storage__": new_cache}
    return TaskState(
        control=ValueControl(None),
        env=env,
        store=new_store,
        kontinuation=k,
        status=ReadyStatus(None),
    )


__all__ = [
    "handle_await",
    "handle_cache_delete",
    "handle_cache_exists",
    "handle_cache_get",
    "handle_cache_put",
    "handle_io",
]
