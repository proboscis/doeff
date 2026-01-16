"""IO and cache effect handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.frames import ContinueValue, ContinueError

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk.state import TaskState
    from doeff.cesk.types import Store
    from doeff.cesk.frames import FrameResult


def handle_io(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    try:
        result = effect.action()
        return ContinueValue(
            value=result,
            env=task_state.env,
            store=store,
            k=task_state.kontinuation,
        )
    except Exception as ex:
        return ContinueError(
            error=ex,
            env=task_state.env,
            store=store,
            k=task_state.kontinuation,
        )


def _get_cache_storage(store: Store) -> dict[str, Any]:
    return store.get("__cache_storage__", {})


def _put_cache_storage(store: Store, cache: dict[str, Any]) -> Store:
    return {**store, "__cache_storage__": cache}


def handle_cache_get(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    cache = _get_cache_storage(store)
    key = effect.key
    if key not in cache:
        return ContinueError(
            error=KeyError(f"Cache key not found: {key!r}"),
            env=task_state.env,
            store=store,
            k=task_state.kontinuation,
        )
    return ContinueValue(
        value=cache[key],
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


def handle_cache_put(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    cache = _get_cache_storage(store)
    new_cache = {**cache, effect.key: effect.value}
    new_store = _put_cache_storage(store, new_cache)
    return ContinueValue(
        value=None,
        env=task_state.env,
        store=new_store,
        k=task_state.kontinuation,
    )


def handle_cache_exists(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    cache = _get_cache_storage(store)
    exists = effect.key in cache
    return ContinueValue(
        value=exists,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


def handle_cache_delete(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    cache = _get_cache_storage(store)
    new_cache = {k: v for k, v in cache.items() if k != effect.key}
    new_store = _put_cache_storage(store, new_cache)
    return ContinueValue(
        value=None,
        env=task_state.env,
        store=new_store,
        k=task_state.kontinuation,
    )


__all__ = [
    "handle_io",
    "handle_cache_get",
    "handle_cache_put",
    "handle_cache_exists",
    "handle_cache_delete",
]
