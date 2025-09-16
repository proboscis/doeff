"""
Cache effects for memoization.

This module provides Cache effects for caching computations.
"""

from typing import Any

from .base import Effect, create_effect_with_trace


class cache:
    """Cache effects for memoization."""

    @staticmethod
    def get(key: Any) -> Effect:
        """Get value from cache. Key can be any serializable object."""
        return create_effect_with_trace("cache.get", key)

    @staticmethod
    def put(key: Any, value: Any, ttl: int | None = None) -> Effect:
        """Put value into cache with optional TTL (in seconds).
        
        Key can be any serializable object (e.g., tuple, FrozenDict).
        """
        return create_effect_with_trace("cache.put", {"key": key, "value": value, "ttl": ttl})


# Uppercase aliases
def CacheGet(key: Any) -> Effect:
    """Cache: Get value from cache. Key can be any serializable object."""
    return create_effect_with_trace("cache.get", key, skip_frames=3)


def CachePut(key: Any, value: Any, ttl: int | None = None) -> Effect:
    """Cache: Put value into cache with optional TTL."""
    return create_effect_with_trace("cache.put", {"key": key, "value": value, "ttl": ttl}, skip_frames=3)


__all__ = [
    "cache",
    "CacheGet",
    "CachePut",
]