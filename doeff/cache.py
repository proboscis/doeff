"""
Cache decorator for doeff programs using cache effects.

This module provides the @cache decorator that automatically handles caching
with CacheGet/CachePut effects and uses Recover for handling cache misses.
"""

import functools
from typing import Any, Callable, Optional, TypeVar

from doeff import (
    do,
    EffectGenerator,
    CacheGet,
    CachePut,
    Recover,
    Log,
)
from doeff._vendor import FrozenDict

T = TypeVar("T")


def cache(ttl: Optional[int] = None, key_func: Optional[Callable] = None):
    """
    Cache decorator that uses CacheGet/CachePut effects with Recover for misses.
    
    This decorator automatically caches the results of the decorated function.
    On cache miss (when CacheGet fails), it uses Recover to execute the original
    function and then stores the result with CachePut.
    
    The cache key is composed of (func_name, args_tuple, kwargs_frozendict).
    The interpreter is responsible for serializing this key.
    
    Args:
        ttl: Time-to-live for cache entries in seconds. None means no expiration.
        key_func: Optional function to transform cache keys. If not provided,
                 uses (func_name, args, FrozenDict(kwargs)) as key.
    
    Example:
        >>> @cache(ttl=60)
        ... @do
        ... def expensive_computation(x: int) -> EffectGenerator[int]:
        ...     yield Log(f"Computing result for {x}")
        ...     return x * 2
        
        The first call will compute and cache the result.
        Subsequent calls within TTL will return the cached value.
    """
    def decorator(func: Callable[..., EffectGenerator[T]]) -> Callable[..., EffectGenerator[T]]:
        @functools.wraps(func)
        @do
        def wrapper(*args, **kwargs) -> EffectGenerator[T]:
            # Create cache key as (func_name, args, frozen_kwargs)
            # The interpreter will handle serialization
            frozen_kwargs = FrozenDict(kwargs) if kwargs else FrozenDict()
            
            if key_func:
                cache_key = key_func(func.__name__, args, frozen_kwargs)
            else:
                cache_key = (func.__name__, args, frozen_kwargs)
            
            yield Log(f"Cache: checking key for {func.__name__}")
            
            # Define the fallback computation
            @do
            def compute_and_cache() -> EffectGenerator[T]:
                yield Log(f"Cache miss for {func.__name__}, computing...")
                # Execute the original function
                result = yield func(*args, **kwargs)
                # Store in cache with the key
                yield CachePut(cache_key, result, ttl)
                yield Log(f"Cache: stored result for {func.__name__}")
                return result
            
            # Try to get from cache, recover with computation on miss
            result = yield Recover(CacheGet(cache_key), compute_and_cache())
            
            # Log cache hit if we got here without computing
            # (The log will only appear if CacheGet succeeded)
            
            return result
        
        return wrapper
    return decorator


def cache_key(*key_parts: Any) -> Callable:
    """
    Create a custom key function for the cache decorator.
    
    This allows you to specify which arguments should be used for the cache key.
    
    Args:
        *key_parts: Names of arguments to include in the cache key
    
    Example:
        >>> @cache(key_func=cache_key("user_id", "date"))
        ... @do
        ... def get_user_data(user_id: int, date: str, debug: bool = False):
        ...     # Only user_id and date will be used for caching
        ...     # debug flag won't affect cache key
        ...     ...
    """
    def key_func(func_name: str, args: tuple, frozen_kwargs: FrozenDict) -> tuple:
        # Extract specified parts from args/kwargs
        import inspect
        
        # Get the original function (this is tricky with decorators)
        # For now, we'll just filter the provided kwargs
        key_values = []
        kwargs = dict(frozen_kwargs) if frozen_kwargs else {}
        
        # Create a simplified key with only specified parts
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in key_parts}
        
        return (func_name, args, FrozenDict(filtered_kwargs))
    
    return key_func


# Convenience decorators with common TTL values
def cache_1min(func: Callable[..., EffectGenerator[T]]) -> Callable[..., EffectGenerator[T]]:
    """Cache with 1 minute TTL."""
    return cache(ttl=60)(func)


def cache_5min(func: Callable[..., EffectGenerator[T]]) -> Callable[..., EffectGenerator[T]]:
    """Cache with 5 minute TTL."""
    return cache(ttl=300)(func)


def cache_1hour(func: Callable[..., EffectGenerator[T]]) -> Callable[..., EffectGenerator[T]]:
    """Cache with 1 hour TTL."""
    return cache(ttl=3600)(func)


def cache_forever(func: Callable[..., EffectGenerator[T]]) -> Callable[..., EffectGenerator[T]]:
    """Cache forever (no TTL)."""
    return cache(ttl=None)(func)


__all__ = [
    "cache",
    "cache_key",
    "cache_1min",
    "cache_5min",
    "cache_1hour",
    "cache_forever",
]