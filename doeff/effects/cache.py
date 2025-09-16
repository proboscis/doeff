"""
Cache effects for memoization.

This module provides Cache effects for caching computations.
"""

from typing import Any

from .base import Effect


class cache:
    """Cache effects for memoization."""

    @staticmethod
    def get(key: Any) -> Effect:
        """Get value from cache. Key can be any serializable object."""
        return Effect("cache.get", key)

    @staticmethod
    def put(key: Any, value: Any, ttl: int | None = None) -> Effect:
        """Put value into cache with optional TTL (in seconds).
        
        Key can be any serializable object (e.g., tuple, FrozenDict).
        """
        return Effect("cache.put", {"key": key, "value": value, "ttl": ttl})


# Uppercase aliases
def CacheGet(key: Any) -> Effect:
    """Cache: Get value from cache. Key can be any serializable object."""
    return cache.get(key)


def CachePut(key: Any, value: Any, ttl: int | None = None) -> Effect:
    """Cache: Put value into cache with optional TTL."""
    return cache.put(key, value, ttl)


__all__ = [
    "cache",
    "CacheGet",
    "CachePut",
]