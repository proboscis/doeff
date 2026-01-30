"""IO and cache effect handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.frames import ContinueError, ContinueValue, FrameResult
from doeff.cesk.types import Store
from doeff.effects.cache import (
    CacheDeleteEffect,
    CacheExistsEffect,
    CacheGetEffect,
    CachePutEffect,
)
from doeff.effects.io import IOPerformEffect

if TYPE_CHECKING:
    from doeff.cesk.runtime.context import HandlerContext


def handle_io(
    effect: IOPerformEffect,
    ctx: HandlerContext,
) -> FrameResult:
    try:
        result = effect.action()
        return ContinueValue(
            value=result,
            env=ctx.task_state.env,
            store=ctx.store,
            k=ctx.task_state.kontinuation,
        )
    except Exception as ex:
        return ContinueError(
            error=ex,
            env=ctx.task_state.env,
            store=ctx.store,
            k=ctx.task_state.kontinuation,
        )


def _get_cache_storage(store: Store) -> dict[str, Any]:
    return store.get("__cache_storage__", {})


def _set_cache_storage(store: Store, cache: dict[str, Any]) -> Store:
    return {**store, "__cache_storage__": cache}


def handle_cache_get(
    effect: CacheGetEffect,
    ctx: HandlerContext,
) -> FrameResult:
    cache = _get_cache_storage(ctx.store)
    key = effect.key
    if key not in cache:
        return ContinueError(
            error=KeyError(f"Cache key not found: {key!r}"),
            env=ctx.task_state.env,
            store=ctx.store,
            k=ctx.task_state.kontinuation,
        )
    return ContinueValue(
        value=cache[key],
        env=ctx.task_state.env,
        store=ctx.store,
        k=ctx.task_state.kontinuation,
    )


def handle_cache_put(
    effect: CachePutEffect,
    ctx: HandlerContext,
) -> FrameResult:
    cache = _get_cache_storage(ctx.store)
    new_cache = {**cache, effect.key: effect.value}
    new_store = _set_cache_storage(ctx.store, new_cache)
    return ContinueValue(
        value=None,
        env=ctx.task_state.env,
        store=new_store,
        k=ctx.task_state.kontinuation,
    )


def handle_cache_exists(
    effect: CacheExistsEffect,
    ctx: HandlerContext,
) -> FrameResult:
    cache = _get_cache_storage(ctx.store)
    exists = effect.key in cache
    return ContinueValue(
        value=exists,
        env=ctx.task_state.env,
        store=ctx.store,
        k=ctx.task_state.kontinuation,
    )


def handle_cache_delete(
    effect: CacheDeleteEffect,
    ctx: HandlerContext,
) -> FrameResult:
    cache = _get_cache_storage(ctx.store)
    new_cache = {k: v for k, v in cache.items() if k != effect.key}
    new_store = _set_cache_storage(ctx.store, new_cache)
    return ContinueValue(
        value=None,
        env=ctx.task_state.env,
        store=new_store,
        k=ctx.task_state.kontinuation,
    )


__all__ = [
    "handle_cache_delete",
    "handle_cache_exists",
    "handle_cache_get",
    "handle_cache_put",
    "handle_io",
]
