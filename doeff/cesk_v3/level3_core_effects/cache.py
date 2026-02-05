"""Cache effects and handler for CESK v3.

Cache effects provide key-value caching within a computation:
- CacheGet(key): Retrieve a cached value (returns CACHE_MISS sentinel if not found)
- CachePut(key, value): Store a value in cache (returns None)
- CacheDelete(key): Remove a value from cache (returns True if deleted, False if not found)
- CacheExists(key): Check if a key exists in cache (returns bool)

Usage:
    from doeff.cesk_v3.level3_core_effects import (
        CacheGet, CachePut, CacheExists, CacheDelete, cache_handler, CACHE_MISS
    )
    from doeff.cesk_v3 import WithHandler, run
    from doeff.do import do

    @do
    def program():
        yield CachePut("key", "value")
        result = yield CacheGet("key")
        if result is CACHE_MISS:
            return "not found"
        return result

    handler, cache = cache_handler()
    result = run(WithHandler(handler, program()))
    # result == "value"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff.cesk_v3.level2_algebraic_effects.frames import EffectBase
from doeff.cesk_v3.level2_algebraic_effects.primitives import Forward, Resume
from doeff.do import do
from doeff.program import Program


class _CacheMiss:
    __slots__ = ()

    def __repr__(self) -> str:
        return "CACHE_MISS"

    def __bool__(self) -> bool:
        return False


CACHE_MISS: Any = _CacheMiss()


@dataclass(frozen=True)
class CacheGetEffect(EffectBase):
    key: Any


@dataclass(frozen=True)
class CachePutEffect(EffectBase):
    key: Any
    value: Any


@dataclass(frozen=True)
class CacheDeleteEffect(EffectBase):
    key: Any


@dataclass(frozen=True)
class CacheExistsEffect(EffectBase):
    key: Any


def CacheGet(key: Any) -> CacheGetEffect:
    return CacheGetEffect(key=key)


def CachePut(key: Any, value: Any) -> CachePutEffect:
    return CachePutEffect(key=key, value=value)


def CacheDelete(key: Any) -> CacheDeleteEffect:
    return CacheDeleteEffect(key=key)


def CacheExists(key: Any) -> CacheExistsEffect:
    return CacheExistsEffect(key=key)


def cache_handler(
    initial_cache: dict[Any, Any] | None = None,
) -> tuple[Any, dict[Any, Any]]:
    """Create a cache handler with optional initial cache contents.

    Returns:
        Tuple of (handler function, cache dict) where:
        - handler: Handler function compatible with WithHandler
        - cache: The underlying cache dict (for inspection after run)
    """
    cache: dict[Any, Any] = dict(initial_cache) if initial_cache else {}

    @do
    def handler(effect: EffectBase) -> Program[Any]:
        if isinstance(effect, CacheGetEffect):
            value = cache.get(effect.key, CACHE_MISS)
            return (yield Resume(value))
        if isinstance(effect, CachePutEffect):
            cache[effect.key] = effect.value
            return (yield Resume(None))
        if isinstance(effect, CacheDeleteEffect):
            deleted = effect.key in cache
            if deleted:
                del cache[effect.key]
            return (yield Resume(deleted))
        if isinstance(effect, CacheExistsEffect):
            return (yield Resume(effect.key in cache))
        forwarded = yield Forward(effect)
        return (yield Resume(forwarded))

    return handler, cache


__all__ = [
    "CACHE_MISS",
    "CacheDelete",
    "CacheDeleteEffect",
    "CacheExists",
    "CacheExistsEffect",
    "CacheGet",
    "CacheGetEffect",
    "CachePut",
    "CachePutEffect",
    "cache_handler",
]
