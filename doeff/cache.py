"""Cache decorator for doeff programs using cache effects."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from doeff.do import do
from doeff.effects.cache import CacheGet, CachePut
from doeff.effects.result import Recover
from doeff.effects.writer import Log
from doeff.types import EffectGenerator
from doeff._vendor import FrozenDict

T = TypeVar("T")


def cache(
    ttl: float | None = None,
    key_func: Callable | None = None,
    *,
    lifecycle: CacheLifecycle | str | None = None,
    storage: CacheStorage | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    policy: CachePolicy | Mapping[str, Any] | None = None,
):
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
        lifecycle: Hint describing expected lifetime of cached values.
        storage: Explicit storage target hint (e.g., "memory", "disk").
        metadata: Arbitrary metadata to attach to the cache policy.
        policy: Pre-built CachePolicy or mapping describing cache behaviour.
    
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
        # Check if func is a KleisliProgram (from @do decorator)
        from doeff.kleisli import KleisliProgram

        # Store the function object for unique identification
        # This ensures different functions have different cache keys
        func_id = id(func)

        if isinstance(func, KleisliProgram):
            # Get the actual function from KleisliProgram if possible
            # Use the object id as part of the cache key for uniqueness
            func_name = f"kleisli_{func_id}"
            wrapped_func = func
            signature_source = func.func
        else:
            func_name = getattr(func, "__name__", f"func_{func_id}")
            wrapped_func = func
            signature_source = func

        try:
            signature = inspect.signature(signature_source)
        except (ValueError, TypeError):
            signature = None

        @do
        def wrapper(*args, **kwargs) -> EffectGenerator[T]:
            # Create cache key as (func_name, args, frozen_kwargs)
            # The interpreter will handle serialization
            if signature is not None:
                try:
                    bound = signature.bind(*args, **kwargs)
                except TypeError:
                    args_for_key = args
                    kwargs_for_key = dict(kwargs)
                else:
                    bound.apply_defaults()
                    args_list: list[Any] = []
                    kwargs_for_key: dict[str, Any] = {}
                    for name, param in signature.parameters.items():
                        value = bound.arguments.get(name)
                        if param.kind in (
                            inspect.Parameter.POSITIONAL_ONLY,
                            inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        ):
                            args_list.append(value)
                        elif param.kind is inspect.Parameter.VAR_POSITIONAL:
                            args_list.extend(value or ())
                        elif param.kind is inspect.Parameter.KEYWORD_ONLY:
                            kwargs_for_key[name] = value
                        elif param.kind is inspect.Parameter.VAR_KEYWORD:
                            if value:
                                kwargs_for_key.update(value)
                    args_for_key = tuple(args_list)
            else:
                args_for_key = args
                kwargs_for_key = dict(kwargs)

            frozen_kwargs = FrozenDict(kwargs_for_key) if kwargs_for_key else FrozenDict()

            if key_func:
                cache_key = key_func(func_name, args_for_key, frozen_kwargs)
            else:
                cache_key = (func_name, args_for_key, frozen_kwargs)

            yield Log(f"Cache: checking key for {func_name}")

            # Define the fallback computation
            @do
            def compute_and_cache() -> EffectGenerator[T]:
                yield Log(f"Cache miss for {func_name}, computing...")
                # Execute the original function
                result = yield wrapped_func(*args, **kwargs)
                # Store in cache with the key
                yield CachePut(
                    cache_key,
                    result,
                    ttl,
                    lifecycle=lifecycle,
                    storage=storage,
                    metadata=metadata,
                    policy=policy,
                )
                yield Log(f"Cache: stored result for {func_name}")
                return result

            # Create a program that tries to get from cache
            @do
            def try_cache_get() -> EffectGenerator[T]:
                return (yield CacheGet(cache_key))

            # Try to get from cache, recover with computation on miss
            result = yield Recover(try_cache_get, compute_and_cache)

            # Log cache hit if we got here without computing
            # (The log will only appear if CacheGet succeeded)

            return result

        # Try to preserve some metadata if possible
        try:
            wrapper.__name__ = getattr(func, "__name__", wrapper.__name__)
            wrapper.__doc__ = getattr(func, "__doc__", wrapper.__doc__)
        except (AttributeError, TypeError):
            # Can't set attributes on some objects like KleisliProgram
            pass

        return wrapper

    return decorator


def cache_key(*key_args: str) -> Callable:
    """
    Create a key function that selects specific arguments for cache key.
    
    Args:
        *key_args: Names of arguments to include in the cache key.
    
    Example:
        >>> @cache(key_func=cache_key("user_id", "date"))
        ... @do
        ... def get_user_activity(user_id: int, date: str, include_details: bool = False):
        ...     # Only user_id and date will be used for the cache key
        ...     # include_details will be ignored for caching
        ...     pass
    """
    def key_function(func_name: str, args: tuple, kwargs: FrozenDict) -> tuple:
        """Extract specified arguments for cache key."""
        # For simplicity, assume key_args refer to positional arguments
        # In a real implementation, you'd want to map parameter names properly

        # Create a simpler key using only specified arguments
        key_values = []
        for i, arg_name in enumerate(key_args):
            if i < len(args):
                key_values.append(args[i])

        return (func_name, tuple(key_values), FrozenDict())

    return key_function


# Convenience decorators
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
    "cache_1hour",
    "cache_1min",
    "cache_5min",
    "cache_forever",
    "cache_key",
]
