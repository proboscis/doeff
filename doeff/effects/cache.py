"""Cache effects for memoization."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any, Callable

from ..cache_policy import (
    CacheLifecycle,
    CachePolicy,
    CacheStorage,
    ensure_cache_policy,
)
from .base import Effect, EffectBase, create_effect_with_trace


@dataclass(frozen=True)
class CacheGetEffect(EffectBase):
    """Requests the cached value for the key and yields the stored payload."""

    key: Any

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "CacheGetEffect":
        return self


@dataclass(frozen=True)
class CachePutEffect(EffectBase):
    """Persists the value under the key and completes after the cache is updated."""

    key: Any
    value: Any
    policy: CachePolicy
    def __post_init__(self):
        import cloudpickle
        cloudpickle.dumps(self.key)
        # so this, is always running fine!

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "CachePutEffect":
        return self


def cache_get(key: Any) -> CacheGetEffect:
    return create_effect_with_trace(CacheGetEffect(key=key))


def cache_put(
    key: Any,
    value: Any,
    ttl: float | None = None,
    *,
    lifecycle: CacheLifecycle | str | None = None,
    storage: CacheStorage | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    policy: CachePolicy | Mapping[str, Any] | None = None,
) -> CachePutEffect:
    cache_policy = ensure_cache_policy(
        ttl=ttl,
        lifecycle=lifecycle,
        storage=storage,
        metadata=metadata,
        policy=policy,
    )
    return create_effect_with_trace(
        CachePutEffect(key=key, value=value, policy=cache_policy)
    )


def CacheGet(key: Any) -> Effect:
    return create_effect_with_trace(CacheGetEffect(key=key), skip_frames=3)


def CachePut(
    key: Any,
    value: Any,
    ttl: float | None = None,
    *,
    lifecycle: CacheLifecycle | str | None = None,
    storage: CacheStorage | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    policy: CachePolicy | Mapping[str, Any] | None = None,
) -> Effect:
    cache_policy = ensure_cache_policy(
        ttl=ttl,
        lifecycle=lifecycle,
        storage=storage,
        metadata=metadata,
        policy=policy,
    )
    return create_effect_with_trace(
        CachePutEffect(key=key, value=value, policy=cache_policy), skip_frames=3
    )


__all__ = [
    "CacheGetEffect",
    "CachePutEffect",
    "cache_get",
    "cache_put",
    "CacheGet",
    "CachePut",
    "CacheLifecycle",
    "CachePolicy",
    "CacheStorage",
]
