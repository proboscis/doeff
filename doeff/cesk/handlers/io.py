"""I/O and cache effect handlers.

Handles effects for I/O operations:
- IOPerformEffect: Perform external I/O
- FutureAwaitEffect: Await an external awaitable
- CacheGetEffect: Get from cache
- CachePutEffect: Put to cache
- CacheDeleteEffect: Delete from cache
- CacheExistsEffect: Check if key exists in cache
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.actions import AwaitExternal, PerformIO
from doeff.cesk.handlers import HandlerContext, HandlerResult

if TYPE_CHECKING:
    from doeff.effects import (
        CacheDeleteEffect,
        CacheExistsEffect,
        CacheGetEffect,
        CachePutEffect,
        FutureAwaitEffect,
        IOPerformEffect,
    )


def handle_io(effect: IOPerformEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle IOPerformEffect: perform external I/O.

    Returns a PerformIO action that the runtime will execute.
    The runtime is responsible for actually calling the I/O function.
    """
    return HandlerResult((PerformIO(effect.action),))


def handle_await(effect: FutureAwaitEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle FutureAwaitEffect: await an external awaitable.

    Returns an AwaitExternal action that the runtime will await.
    """
    return HandlerResult((AwaitExternal(effect.awaitable),))


# ============================================================================
# Cache Handlers
# ============================================================================


def _get_cache_storage(ctx: HandlerContext) -> dict[str, Any]:
    """Get or create the cache storage from store."""
    return ctx.store.get("__cache_storage__", {})


def handle_cache_get(effect: CacheGetEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle CacheGetEffect: get value from cache.

    Returns the cached value or None if not found.
    """
    cache = _get_cache_storage(ctx)
    value = cache.get(effect.key)
    return HandlerResult.resume(value)


def handle_cache_put(effect: CachePutEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle CachePutEffect: put value to cache.

    Stores the value in cache and resumes with None.
    """
    cache = dict(_get_cache_storage(ctx))
    cache[effect.key] = effect.value
    new_store = dict(ctx.store)
    new_store["__cache_storage__"] = cache
    return HandlerResult.resume_with_store(None, new_store)


def handle_cache_delete(effect: CacheDeleteEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle CacheDeleteEffect: delete value from cache.

    Removes the key from cache if it exists. Resumes with True if
    key was present, False otherwise.
    """
    cache = dict(_get_cache_storage(ctx))
    was_present = effect.key in cache
    if was_present:
        del cache[effect.key]
    new_store = dict(ctx.store)
    new_store["__cache_storage__"] = cache
    return HandlerResult.resume_with_store(was_present, new_store)


def handle_cache_exists(effect: CacheExistsEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle CacheExistsEffect: check if key exists in cache.

    Returns True if key exists, False otherwise.
    """
    cache = _get_cache_storage(ctx)
    exists = effect.key in cache
    return HandlerResult.resume(exists)


__all__ = [
    "handle_io",
    "handle_await",
    "handle_cache_get",
    "handle_cache_put",
    "handle_cache_delete",
    "handle_cache_exists",
]
