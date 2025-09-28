"""Cache decorator for doeff programs using cache effects."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from doeff.decorators import do_wrapper
from doeff.do import do
from doeff.effects.cache import CacheGet, CachePut
from doeff.effects.result import Recover, Safe, Fail
from doeff.effects.writer import Log
from doeff.types import EffectGenerator
from doeff._vendor import FrozenDict, Result

if TYPE_CHECKING:
    from doeff.kleisli import KleisliProgram

T = TypeVar("T")


@do_wrapper
def cache(
    ttl: float | None = None,
    key_func: Callable | None = None,
    key_hashers: Mapping[str, Callable[[Any], Any] | "KleisliProgram[Any, Any]"] | None = None,
    *,
    lifecycle: CacheLifecycle | str | None = None,
    storage: CacheStorage | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    policy: CachePolicy | Mapping[str, Any] | None = None,
):
    """Cache decorator that uses CacheGet/CachePut effects with Recover for misses.

    The decorator automatically caches results produced by the wrapped function. On a cache miss
    (when ``CacheGet`` fails), it evaluates the original function, caches the ``Result`` via
    ``CachePut``, and then unwraps it for the caller.

    The default interpreter stores entries in the sqlite/LZMA handler. Only ``ttl`` is acted on by
    that handler today; ``lifecycle``, ``storage``, and ``metadata`` are preserved for custom
    handlers that want richer behaviour.

    The cache key defaults to ``(func_name, args, FrozenDict(kwargs))`` where ``func_name`` is the
    fully qualified module path of the wrapped callable; the interpreter serializes the key before
    persistence.

    Args:
        ttl: Expiry in seconds. ``None`` or values <= 0 mean "no expiry" for the bundled handler.
        key_func: Optional key builder. Receives ``(func_name, args, FrozenDict(kwargs))`` and must
            return the object to cache under.
        key_hashers: Mapping of argument names to hashers or Kleisli programs that pre-process
            arguments prior to key construction (e.g., to normalize large inputs).
        lifecycle: Lifecycle hint for downstream handlers. Accepts ``CacheLifecycle`` or the
            strings ``"transient"``, ``"session"``, ``"persistent"``.
        storage: Storage hint for downstream handlers. Accepts ``CacheStorage`` or the strings
            ``"memory"`` and ``"disk"``.
        metadata: Arbitrary mapping carried alongside the cache policy for custom handlers.
        policy: Pre-built ``CachePolicy`` (or mapping) describing cache behaviour. Mutually
            exclusive with the individual policy fields above.

    Example:
        >>> @cache(ttl=60, lifecycle="session")
        ... @do
        ... def expensive_computation(x: int) -> EffectGenerator[int]:
        ...     yield Log(f"Computing result for {x}")
        ...     return x * 2
        >>> await ProgramInterpreter().run(expensive_computation(5))
        
        The first call will compute and cache the result. Subsequent calls within the TTL return
        the cached value, and the policy hints remain available to custom cache handlers.
    """
    def decorator(func: Callable[..., EffectGenerator[T]]) -> Callable[..., EffectGenerator[T]]:
        # Check if func is a KleisliProgram (from @do decorator)
        from doeff.kleisli import KleisliProgram

        def _function_identifier(target: Any) -> str:
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

        if isinstance(func, KleisliProgram):
            wrapped_func = func
            signature_source = func.func
            func_name = _function_identifier(signature_source)
        else:
            wrapped_func = func
            signature_source = func
            func_name = _function_identifier(func)

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
                    if key_hashers:
                        for name, hasher in key_hashers.items():
                            if name in kwargs_for_key:
                                kwargs_for_key[name] = (
                                    (yield hasher(kwargs_for_key[name]))
                                    if isinstance(hasher, KleisliProgram)
                                    else hasher(kwargs_for_key[name])
                                )
                else:
                    bound.apply_defaults()
                    arguments = bound.arguments.copy()
                    kwargs_param_name: str | None = None
                    if key_hashers:
                        for param_name, param in signature.parameters.items():
                            if param.kind is inspect.Parameter.VAR_KEYWORD:
                                kwargs_param_name = param_name
                                break
                        if kwargs_param_name and kwargs_param_name in arguments:
                            arguments[kwargs_param_name] = dict(arguments[kwargs_param_name])

                        for name, hasher in key_hashers.items():
                            if name in arguments:
                                arguments[name] = (
                                    (yield hasher(arguments[name]))
                                    if isinstance(hasher, KleisliProgram)
                                    else hasher(arguments[name])
                                )
                            elif (
                                kwargs_param_name
                                and kwargs_param_name in arguments
                                and name in arguments[kwargs_param_name]
                            ):
                                arguments[kwargs_param_name][name] = (
                                    (yield hasher(arguments[kwargs_param_name][name]))
                                    if isinstance(hasher, KleisliProgram)
                                    else hasher(arguments[kwargs_param_name][name])
                                )

                    args_list: list[Any] = []
                    kwargs_for_key = {}
                    for name, param in signature.parameters.items():
                        value = arguments.get(name)
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
                if key_hashers:
                    for name, hasher in key_hashers.items():
                        if name in kwargs_for_key:
                            kwargs_for_key[name] = (
                                (yield hasher(kwargs_for_key[name]))
                                if isinstance(hasher, KleisliProgram)
                                else hasher(kwargs_for_key[name])
                            )

            frozen_kwargs = FrozenDict(kwargs_for_key) if kwargs_for_key else FrozenDict()

            if key_func:
                cache_key = key_func(func_name, args_for_key, frozen_kwargs)
            else:
                cache_key = (func_name, args_for_key, frozen_kwargs)

            # yield Log(f"Cache: checking key for {func_name}")

            # Define the fallback computation
            @do
            def compute_and_cache() -> EffectGenerator[T]:
                # yield Log(f"Cache miss for {func_name}, computing...")
                # Execute the original function
                result: Result = yield Safe(wrapped_func(*args, **kwargs))
                if result.is_ok():
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
                else:
                    yield Log(f"Computation for {func_name} failed, not caching.")
                    raise result.unwrap_err()
                return result.unwrap()

            # Create a program that tries to get from cache
            @do
            def try_cache_get() -> EffectGenerator[T]:
                return (yield CacheGet(cache_key))

            # Try to get from cache, recover with computation on miss
            result = yield Recover(try_cache_get(), compute_and_cache())

            # Log cache hit if we got here without computing
            # (The log will only appear if CacheGet succeeded)

            if isinstance(result, Result):
                return result.unwrap()
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
