"""
Cache effects for memoization.

This module provides Cache effects for caching computations.
"""

from collections.abc import Mapping
from typing import Any

from ..cache_policy import (
    CacheLifecycle,
    CachePolicy,
    CacheStorage,
    ensure_cache_policy,
)
from .base import Effect, create_effect_with_trace


class cache:
    """Cache effects for memoization."""

    @staticmethod
    def get(key: Any) -> Effect:
        """Get value from cache. Key can be any serializable object."""
        return create_effect_with_trace("cache.get", key)

    @staticmethod
    def put(
        key: Any,
        value: Any,
        ttl: float | None = None,
        *,
        lifecycle: CacheLifecycle | str | None = None,
        storage: CacheStorage | str | None = None,
        metadata: Mapping[str, Any] | None = None,
        policy: CachePolicy | Mapping[str, Any] | None = None,
    ) -> Effect:
        """Put value into cache with optional TTL and policy hints.

        Key can be any serializable object (e.g., tuple, FrozenDict).
        """

        cache_policy = ensure_cache_policy(
            ttl=ttl,
            lifecycle=lifecycle,
            storage=storage,
            metadata=metadata,
            policy=policy,
        )
        payload = {"key": key, "value": value, "policy": cache_policy}
        if cache_policy.ttl is not None:
            payload["ttl"] = cache_policy.ttl
        return create_effect_with_trace("cache.put", payload)


# Uppercase aliases
def CacheGet(key: Any) -> Effect:
    """Cache: Get value from cache. Key can be any serializable object."""
    return create_effect_with_trace("cache.get", key, skip_frames=3)


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
    """Cache: Put value into cache with optional TTL and lifecycle hints."""

    cache_policy = ensure_cache_policy(
        ttl=ttl,
        lifecycle=lifecycle,
        storage=storage,
        metadata=metadata,
        policy=policy,
    )
    payload = {"key": key, "value": value, "policy": cache_policy}
    if cache_policy.ttl is not None:
        payload["ttl"] = cache_policy.ttl
    return create_effect_with_trace("cache.put", payload, skip_frames=3)


__all__ = [
    "CacheGet",
    "CacheLifecycle",
    "CachePolicy",
    "CachePut",
    "CacheStorage",
    "cache",
]
