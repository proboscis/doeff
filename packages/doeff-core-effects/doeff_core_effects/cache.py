"""Cache decorator for doeff programs using cache effects.

The @cache decorator memoizes @do function results via CacheGet/CachePut effects.
Requires a cache handler (cache_handler, in_memory_cache_handler, or sqlite_cache_handler)
to be installed via WithHandler.

NOTE: The full @cache decorator with execution context-based call site attribution
is not yet ported. This module provides the core cache helpers and a simplified
@cache decorator.
"""

import os
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from doeff import do
from doeff_core_effects.cache_policy import CacheLifecycle, CachePolicy, CacheStorage, ensure_cache_policy
from doeff_core_effects.cache_effects import CacheExists, CacheGet, CachePut, CacheGetEffect
from doeff_core_effects.cache_handlers import content_address

T = TypeVar("T")

CACHE_PATH_ENV_KEY = "DOEFF_CACHE_PATH"


def persistent_cache_path() -> Path:
    """Return the persistent cache path from env or default."""
    env_path = os.environ.get(CACHE_PATH_ENV_KEY)
    if env_path:
        return Path(env_path)
    return Path(tempfile.gettempdir()) / "doeff_cache"


def clear_persistent_cache() -> None:
    """Clear the persistent cache directory."""
    path = persistent_cache_path()
    if path.exists():
        import shutil
        shutil.rmtree(path)


def cache_key(fn: Callable, *args: Any, **kwargs: Any) -> str:
    """Compute a cache key for a function call."""
    return content_address((fn.__qualname__, args, tuple(sorted(kwargs.items()))))


def cache(
    fn: Callable | None = None,
    *,
    ttl: float | None = None,
    lifecycle: CacheLifecycle | str | None = None,
    storage: CacheStorage | str | None = None,
    key_fn: Callable[..., str] | None = None,
) -> Callable:
    """Decorator that memoizes a @do function via cache effects.

    Usage:
        @cache
        @do
        def my_func(x):
            result = yield expensive_computation(x)
            return result

    Or with options:
        @cache(ttl=3600)
        @do
        def my_func(x):
            ...
    """
    def decorator(func: Callable) -> Callable:
        compute_key = key_fn or (lambda *a, **kw: cache_key(func, *a, **kw))
        policy = ensure_cache_policy(
            ttl=ttl,
            lifecycle=lifecycle,
            storage=storage,
        )

        @do
        def cached_fn(*args, **kwargs):
            key = compute_key(*args, **kwargs)

            # Check cache
            exists = yield CacheExists(key)
            if exists:
                value = yield CacheGet(key)
                return value

            # Cache miss — run the function
            result = yield func(*args, **kwargs)

            # Store in cache
            yield CachePut(key, result, policy=policy)
            return result

        # Preserve metadata
        cached_fn.__name__ = getattr(func, "__name__", "<cached>")
        cached_fn.__qualname__ = getattr(func, "__qualname__", "<cached>")
        cached_fn.__doc__ = getattr(func, "__doc__", None)
        cached_fn.__wrapped__ = func

        return cached_fn

    if fn is not None:
        return decorator(fn)
    return decorator


# Convenience presets
def cache_1min(fn: Callable) -> Callable:
    return cache(fn, ttl=60)

def cache_5min(fn: Callable) -> Callable:
    return cache(fn, ttl=300)

def cache_1hour(fn: Callable) -> Callable:
    return cache(fn, ttl=3600)

def cache_forever(fn: Callable) -> Callable:
    return cache(fn, ttl=None)


__all__ = [
    "CACHE_PATH_ENV_KEY",
    "cache",
    "cache_1hour",
    "cache_1min",
    "cache_5min",
    "cache_forever",
    "cache_key",
    "clear_persistent_cache",
    "persistent_cache_path",
]
