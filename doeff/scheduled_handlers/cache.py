"""Cache effect handlers.

Direct ScheduledEffectHandler implementations for CacheGetEffect,
CachePutEffect, CacheDeleteEffect, and CacheExistsEffect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.runtime import HandlerResult, Resume

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store


def handle_cache_get(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    storage = store.get("__cache_storage__")
    if storage is None:
        return Resume(None, store)
    value = storage.get(effect.key)
    return Resume(value, store)


def handle_cache_put(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    storage = store.get("__cache_storage__")
    if storage is not None:
        storage.put(effect.key, effect.value)
    return Resume(None, store)


def handle_cache_delete(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    storage = store.get("__cache_storage__")
    if storage is None:
        return Resume(False, store)
    result = storage.delete(effect.key)
    return Resume(result, store)


def handle_cache_exists(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    storage = store.get("__cache_storage__")
    if storage is None:
        return Resume(False, store)
    result = storage.exists(effect.key)
    return Resume(result, store)


__all__ = [
    "handle_cache_get",
    "handle_cache_put",
    "handle_cache_delete",
    "handle_cache_exists",
]
