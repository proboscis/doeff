"""IO and cache effect handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.frames import ContinueValue

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk.frames import FrameResult
    from doeff.cesk.state import TaskState
    from doeff.cesk.types import Store


def handle_io(effect: EffectBase, task: TaskState, store: Store) -> FrameResult:
    from doeff.effects.io import IOPerformEffect

    if not isinstance(effect, IOPerformEffect):
        raise TypeError(f"Expected IOPerformEffect, got {type(effect).__name__}")

    result = effect.action()

    return ContinueValue(
        value=result,
        env=task.env,
        store=store,
        k=task.kontinuation,
    )


def handle_cache_get(effect: EffectBase, task: TaskState, store: Store) -> FrameResult:
    from doeff.effects.cache import CacheGetEffect

    if not isinstance(effect, CacheGetEffect):
        raise TypeError(f"Expected CacheGetEffect, got {type(effect).__name__}")

    cache_storage = store.get("__cache_storage__", {})

    try:
        value = cache_storage[effect.key]
    except KeyError as e:
        raise KeyError(f"Cache key not found: {effect.key!r}") from e

    return ContinueValue(
        value=value,
        env=task.env,
        store=store,
        k=task.kontinuation,
    )


def handle_cache_put(effect: EffectBase, task: TaskState, store: Store) -> FrameResult:
    from doeff.effects.cache import CachePutEffect

    if not isinstance(effect, CachePutEffect):
        raise TypeError(f"Expected CachePutEffect, got {type(effect).__name__}")

    if "__cache_storage__" not in store:
        store["__cache_storage__"] = {}

    store["__cache_storage__"][effect.key] = effect.value

    return ContinueValue(
        value=None,
        env=task.env,
        store=store,
        k=task.kontinuation,
    )


def handle_cache_exists(
    effect: EffectBase, task: TaskState, store: Store
) -> FrameResult:
    from doeff.effects.cache import CacheExistsEffect

    if not isinstance(effect, CacheExistsEffect):
        raise TypeError(f"Expected CacheExistsEffect, got {type(effect).__name__}")

    cache_storage = store.get("__cache_storage__", {})
    exists = effect.key in cache_storage

    return ContinueValue(
        value=exists,
        env=task.env,
        store=store,
        k=task.kontinuation,
    )


def handle_cache_delete(
    effect: EffectBase, task: TaskState, store: Store
) -> FrameResult:
    from doeff.effects.cache import CacheDeleteEffect

    if not isinstance(effect, CacheDeleteEffect):
        raise TypeError(f"Expected CacheDeleteEffect, got {type(effect).__name__}")

    cache_storage = store.get("__cache_storage__", {})

    if effect.key in cache_storage:
        del cache_storage[effect.key]
        deleted = True
    else:
        deleted = False

    return ContinueValue(
        value=deleted,
        env=task.env,
        store=store,
        k=task.kontinuation,
    )


__all__ = [
    "handle_io",
    "handle_cache_get",
    "handle_cache_put",
    "handle_cache_exists",
    "handle_cache_delete",
]
