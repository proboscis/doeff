"""Cache effects for memoization."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

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
        self, transform: Callable[[Effect], Effect | Program]
    ) -> CacheGetEffect:
        return self


@dataclass(frozen=True)
class CachePutEffect(EffectBase):
    """Persists the value under the key and completes after the cache is updated."""

    key: Any
    value: Any
    policy: CachePolicy

    def __post_init__(self):
        if not isinstance(self.policy, CachePolicy):
            raise TypeError(
                "policy must be CachePolicy, got "
                f"{type(self.policy).__name__}"
            )
        import cloudpickle
        cloudpickle.dumps(self.key)
        # so this, is always running fine!

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> CachePutEffect:
        return self


@dataclass(frozen=True)
class CacheDeleteEffect(EffectBase):
    """Deletes the value under the key and returns True if deleted, False otherwise."""

    key: Any

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> CacheDeleteEffect:
        return self


@dataclass(frozen=True)
class CacheExistsEffect(EffectBase):
    """Checks if a key exists in the cache and returns True or False."""

    key: Any

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> CacheExistsEffect:
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


def cache_delete(key: Any) -> CacheDeleteEffect:
    return create_effect_with_trace(CacheDeleteEffect(key=key))


def cache_exists(key: Any) -> CacheExistsEffect:
    return create_effect_with_trace(CacheExistsEffect(key=key))


def CacheDelete(key: Any) -> Effect:
    return create_effect_with_trace(CacheDeleteEffect(key=key), skip_frames=3)


def CacheExists(key: Any) -> Effect:
    return create_effect_with_trace(CacheExistsEffect(key=key), skip_frames=3)


__all__ = [
    "CacheDelete",
    "CacheDeleteEffect",
    "CacheExists",
    "CacheExistsEffect",
    "CacheGet",
    "CacheGetEffect",
    "CacheLifecycle",
    "CachePolicy",
    "CachePut",
    "CachePutEffect",
    "CacheStorage",
    "cache_delete",
    "cache_exists",
    "cache_get",
    "cache_put",
]
