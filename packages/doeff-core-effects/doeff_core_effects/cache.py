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

from frozendict import frozendict as FrozenDict

from doeff import do
from doeff_core_effects.memo_policy import Lifecycle, MemoPolicy, ensure_memo_policy
from doeff_core_effects.memo_effects import MemoExists, MemoGet, MemoPut, MemoGetEffect
from doeff_core_effects.memo_handlers import content_address

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


def _function_identifier(target: Any) -> str:
    """Return a descriptive identifier for a callable (module.qualname)."""
    module = getattr(target, "__module__", None)
    qualname = getattr(target, "__qualname__", None)
    name = getattr(target, "__name__", None)

    parts: list[str] = []
    if module:
        parts.append(module)
    if qualname:
        parts.append(qualname)
    elif name:
        parts.append(name)

    if parts:
        return ".".join(parts)

    func_attr = getattr(target, "func", None)
    if func_attr is not None and func_attr is not target:
        inner = _function_identifier(func_attr)
        cls = target.__class__
        return f"{cls.__module__}.{cls.__qualname__}({inner})"

    cls = target.__class__
    return f"{cls.__module__}.{cls.__qualname__}"


def default_cache_key(func_name: str, args: tuple, kwargs: FrozenDict) -> tuple:
    """Default cache key: (func_name, args, FrozenDict(kwargs))."""
    return (func_name, args, kwargs)


def cache(
    ttl: float | None = None,
    key_func: Callable | None = None,
    *,
    lifecycle: Lifecycle | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    policy: MemoPolicy | Mapping[str, Any] | None = None,
) -> Callable:
    """Cache decorator that uses MemoGet/MemoPut effects for memoization.

    The cache key defaults to ``(func_name, args, FrozenDict(kwargs))`` where
    ``func_name`` is the fully qualified module path of the wrapped callable.

    Args:
        ttl: Expiry in seconds. None means no expiry.
        key_func: Optional key builder. Receives (func_name, args, FrozenDict(kwargs)).
        lifecycle: Lifecycle hint for custom handlers.
        metadata: Extra metadata for custom handlers.
        policy: Full MemoPolicy (overrides ttl/lifecycle/metadata).
    """
    memo_policy = ensure_memo_policy(
        ttl=ttl,
        lifecycle=lifecycle,
        metadata=metadata,
        policy=policy,
    )

    def decorator(func: Callable) -> Callable:
        func_name = _function_identifier(func)

        @do
        def cached_fn(*args, **kwargs):
            frozen_kwargs = FrozenDict(kwargs) if kwargs else FrozenDict()
            cache_key_obj = (
                key_func(func_name, args, frozen_kwargs)
                if key_func
                else (func_name, args, frozen_kwargs)
            )

            # Check memo
            if (yield MemoExists(cache_key_obj)):
                try:
                    cached_value = yield MemoGet(cache_key_obj)
                    return cached_value
                except KeyError:
                    pass  # memo miss — compute below

            # Memo miss — run the function
            result = yield func(*args, **kwargs)

            # Store in memo
            yield MemoPut(cache_key_obj, result, policy=memo_policy)
            return result

        # Preserve metadata
        for attr in ("__name__", "__qualname__", "__doc__", "__module__"):
            val = getattr(func, attr, None)
            if val is not None:
                try:
                    setattr(cached_fn, attr, val)
                except (AttributeError, TypeError):
                    pass

        return cached_fn

    return decorator


# Convenience presets
cache_1min = cache(ttl=60)
cache_5min = cache(ttl=300)
cache_1hour = cache(ttl=3600)
cache_forever = cache(ttl=None)


__all__ = [
    "CACHE_PATH_ENV_KEY",
    "FrozenDict",
    "cache",
    "cache_1hour",
    "cache_1min",
    "cache_5min",
    "cache_forever",
    "clear_persistent_cache",
    "default_cache_key",
    "persistent_cache_path",
]
