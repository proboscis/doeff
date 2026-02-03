"""Cache effect handler.

Handles CacheGet, CachePut, CacheExists, CacheDelete effects.
Uses __cache_storage__ in the store for persistence.
"""

from __future__ import annotations

from doeff.do import do
from doeff._types_internal import EffectBase
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.state import CESKState
from doeff.effects.cache import (
    CacheDeleteEffect,
    CacheExistsEffect,
    CacheGetEffect,
    CachePutEffect,
)


@do
def cache_handler(effect: EffectBase, ctx: HandlerContext):
    """Handles cache effects using __cache_storage__ in the store."""
    store = dict(ctx.store)

    if isinstance(effect, CacheGetEffect):
        cache = store.get("__cache_storage__", {})
        key = effect.key
        if key not in cache:
            return CESKState.with_error(
                KeyError(f"Cache key not found: {key!r}"),
                ctx.env, store, ctx.k
            )
        return CESKState.with_value(cache[key], ctx.env, store, ctx.k)

    if isinstance(effect, CachePutEffect):
        cache = store.get("__cache_storage__", {})
        new_cache = {**cache, effect.key: effect.value}
        new_store = {**store, "__cache_storage__": new_cache}
        return CESKState.with_value(None, ctx.env, new_store, ctx.k)

    if isinstance(effect, CacheExistsEffect):
        cache = store.get("__cache_storage__", {})
        exists = effect.key in cache
        return CESKState.with_value(exists, ctx.env, store, ctx.k)

    if isinstance(effect, CacheDeleteEffect):
        cache = store.get("__cache_storage__", {})
        new_cache = {k: v for k, v in cache.items() if k != effect.key}
        new_store = {**store, "__cache_storage__": new_cache}
        return CESKState.with_value(None, ctx.env, new_store, ctx.k)

    result = yield effect
    return result


__all__ = ["cache_handler"]
