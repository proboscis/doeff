"""Cache effects for memoization."""

from collections.abc import Mapping
from typing import Any

from doeff_core_effects.cache_policy import (
    CacheLifecycle,
    CachePolicy,
    CacheStorage,
    ensure_cache_policy,
)
from doeff_vm import EffectBase


class CacheGetEffect(EffectBase):
    """Requests the cached value for the key."""
    def __init__(self, key):
        super().__init__()
        self.key = key

    def __repr__(self):
        return f"CacheGet({self.key!r})"


class CachePutEffect(EffectBase):
    """Persists the value under the key."""
    def __init__(self, key, value, policy):
        super().__init__()
        if not isinstance(policy, CachePolicy):
            raise TypeError(f"policy must be CachePolicy, got {type(policy).__name__}")
        self.key = key
        self.value = value
        self.policy = policy

    def __repr__(self):
        return f"CachePut({self.key!r}, ...)"


class CacheDeleteEffect(EffectBase):
    """Deletes the value under the key."""
    def __init__(self, key):
        super().__init__()
        self.key = key

    def __repr__(self):
        return f"CacheDelete({self.key!r})"


class CacheExistsEffect(EffectBase):
    """Checks if a key exists in the cache."""
    def __init__(self, key):
        super().__init__()
        self.key = key

    def __repr__(self):
        return f"CacheExists({self.key!r})"


# Convenience constructors (lowercase)

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


def cache_delete(key: Any) -> CacheDeleteEffect:
    return CacheDeleteEffect(key=key)


def cache_exists(key: Any) -> CacheExistsEffect:
    return CacheExistsEffect(key=key)


# Capitalized aliases
CacheGet = cache_get
CachePut = cache_put
CacheDelete = cache_delete
CacheExists = cache_exists


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
