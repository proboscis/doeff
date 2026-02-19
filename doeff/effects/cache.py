"""Cache effects for memoization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..cache_policy import (
    CacheLifecycle,
    CachePolicy,
    CacheStorage,
    ensure_cache_policy,
)
from .base import Effect, EffectBase


@dataclass(frozen=True)
class CacheGetEffect(EffectBase):
    """Requests the cached value for the key and yields the stored payload."""

    key: Any


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


@dataclass(frozen=True)
class CacheDeleteEffect(EffectBase):
    """Deletes the value under the key and returns True if deleted, False otherwise."""

    key: Any


@dataclass(frozen=True)
class CacheExistsEffect(EffectBase):
    """Checks if a key exists in the cache and returns True or False."""

    key: Any


def cache_get(key: Any) -> CacheGetEffect:
    return CacheGetEffect(key=key)


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
    return CachePutEffect(key=key, value=value, policy=cache_policy)


def CacheGet(key: Any) -> Effect:
    return CacheGetEffect(key=key)


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
    return CachePutEffect(key=key, value=value, policy=cache_policy)


def cache_delete(key: Any) -> CacheDeleteEffect:
    return CacheDeleteEffect(key=key)


def cache_exists(key: Any) -> CacheExistsEffect:
    return CacheExistsEffect(key=key)


def CacheDelete(key: Any) -> Effect:
    return CacheDeleteEffect(key=key)


def CacheExists(key: Any) -> Effect:
    return CacheExistsEffect(key=key)


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
