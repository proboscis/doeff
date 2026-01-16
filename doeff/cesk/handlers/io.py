from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.cesk.handlers import HandlerContext, PerformAction, ResumeWith, register_handler
from doeff.cesk.actions import PerformIO, AwaitExternal
from doeff.cesk.types import FutureId

if TYPE_CHECKING:
    from doeff.effects import IOEffect, FutureAwaitEffect, CacheGetEffect, CachePutEffect


@register_handler(type(None))
def handle_io(effect: IOEffect, ctx: HandlerContext) -> PerformAction:
    from doeff.effects import IOEffect
    
    if not isinstance(effect, IOEffect):
        raise TypeError(f"Expected IOEffect, got {type(effect)}")
    
    return PerformAction(PerformIO(
        io_function=effect.function,
        args=getattr(effect, 'args', ()),
        kwargs=getattr(effect, 'kwargs', None),
    ))


@register_handler(type(None))
def handle_await(effect: FutureAwaitEffect, ctx: HandlerContext) -> PerformAction:
    from doeff.effects import FutureAwaitEffect
    
    if not isinstance(effect, FutureAwaitEffect):
        raise TypeError(f"Expected FutureAwaitEffect, got {type(effect)}")
    
    future_id_counter = ctx.store.get("__future_id_counter__", 0)
    future_id = FutureId(future_id_counter)
    ctx.store["__future_id_counter__"] = future_id_counter + 1
    
    return PerformAction(AwaitExternal(effect.awaitable, future_id))


@register_handler(type(None))
def handle_cache_get(effect: CacheGetEffect, ctx: HandlerContext) -> ResumeWith:
    from doeff.effects import CacheGetEffect
    
    if not isinstance(effect, CacheGetEffect):
        raise TypeError(f"Expected CacheGetEffect, got {type(effect)}")
    
    cache_storage = ctx.store.get("__cache_storage__", {})
    
    if effect.key in cache_storage:
        return ResumeWith(cache_storage[effect.key])
    
    raise KeyError(f"Cache key '{effect.key}' not found")


@register_handler(type(None))
def handle_cache_put(effect: CachePutEffect, ctx: HandlerContext) -> ResumeWith:
    from doeff.effects import CachePutEffect
    
    if not isinstance(effect, CachePutEffect):
        raise TypeError(f"Expected CachePutEffect, got {type(effect)}")
    
    cache_storage = ctx.store.get("__cache_storage__", {})
    cache_storage[effect.key] = effect.value
    ctx.store["__cache_storage__"] = cache_storage
    
    return ResumeWith(None)


__all__ = [
    "handle_io",
    "handle_await",
    "handle_cache_get",
    "handle_cache_put",
]
