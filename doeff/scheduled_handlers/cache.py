"""Durable cache effect handlers.

Direct ScheduledEffectHandler implementations for DurableCacheGet,
DurableCachePut, DurableCacheDelete, and DurableCacheExists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.runtime import HandlerResult, Resume

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store
    from doeff.runtime import Continuation, Scheduler


def handle_durable_cache_get(
    effect: EffectBase,
    env: Environment,
    store: Store,
    k: Continuation,
    scheduler: Scheduler | None,
) -> HandlerResult:
    """Handle DurableCacheGet - retrieves value from durable storage."""
    storage = store.get("__durable_storage__")
    if storage is None:
        return Resume(None, store)
    value = storage.get(effect.key)
    return Resume(value, store)


def handle_durable_cache_put(
    effect: EffectBase,
    env: Environment,
    store: Store,
    k: Continuation,
    scheduler: Scheduler | None,
) -> HandlerResult:
    """Handle DurableCachePut - stores value in durable storage."""
    storage = store.get("__durable_storage__")
    if storage is not None:
        storage.put(effect.key, effect.value)
    return Resume(None, store)


def handle_durable_cache_delete(
    effect: EffectBase,
    env: Environment,
    store: Store,
    k: Continuation,
    scheduler: Scheduler | None,
) -> HandlerResult:
    """Handle DurableCacheDelete - deletes value from durable storage."""
    storage = store.get("__durable_storage__")
    if storage is None:
        return Resume(False, store)
    result = storage.delete(effect.key)
    return Resume(result, store)


def handle_durable_cache_exists(
    effect: EffectBase,
    env: Environment,
    store: Store,
    k: Continuation,
    scheduler: Scheduler | None,
) -> HandlerResult:
    """Handle DurableCacheExists - checks if value exists in durable storage."""
    storage = store.get("__durable_storage__")
    if storage is None:
        return Resume(False, store)
    result = storage.exists(effect.key)
    return Resume(result, store)


__all__ = [
    "handle_durable_cache_get",
    "handle_durable_cache_put",
    "handle_durable_cache_delete",
    "handle_durable_cache_exists",
]
