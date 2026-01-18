"""Cache decorator for doeff programs using cache effects."""

from __future__ import annotations

import inspect
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from doeff._vendor import FrozenDict, Result
from doeff.cache_policy import CacheLifecycle, CachePolicy, CacheStorage
from doeff.decorators import do_wrapper
from doeff.do import do
from doeff.effects.cache import CacheGet, CachePut
from doeff.effects.callstack import ProgramCallStack
from doeff.effects.result import Safe
from doeff.effects.writer import Tell, slog
from doeff.types import EffectCreationContext, EffectGenerator


class CacheComputationError(RuntimeError):
    """Error raised when the cached computation fails."""

    def __init__(
        self,
        func_name: str,
        call_args: tuple[Any, ...],
        call_kwargs: dict[str, Any],
        call_site: EffectCreationContext | None = None,
    ) -> None:
        location_suffix = (
            f" at {call_site.format_location()}" if call_site is not None else ""
        )
        message = (
            f"Cache computation for {func_name} failed"
            f" with args={call_args!r} kwargs={call_kwargs!r}{location_suffix}"
        )
        super().__init__(message)
        self.func_name = func_name
        self.call_args = call_args
        self.call_kwargs = call_kwargs
        self.call_site = call_site

        if call_site is not None and hasattr(self, "add_note"):
            self.add_note(
                f"Cache-decorated call originated at {call_site.format_location()}"
            )

if TYPE_CHECKING:
    from doeff.kleisli import KleisliProgram

T = TypeVar("T")


def _function_identifier(target: Any) -> str:
    """Return a descriptive identifier for a callable or callable-like object."""
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


def _safe_signature(callable_obj: Callable[..., Any] | None) -> inspect.Signature | None:
    """Return inspect.Signature if possible, otherwise None."""
    if callable_obj is None:
        return None
    try:
        return inspect.signature(callable_obj)
    except (ValueError, TypeError):
        return None


def _is_internal_cache_filename(filename: str | None) -> bool:
    if not filename:
        return False
    normalized = filename.replace("\\", "/")
    return "doeff/cache.py" in normalized


def _truncate_for_log(obj: Any, max_len: int = 200) -> str:
    """Truncate object representation for logging to avoid massive log output."""
    try:
        repr_str = repr(obj)
    except Exception:
        repr_str = f"<{type(obj).__name__} (repr failed)>"

    if len(repr_str) <= max_len:
        return repr_str

    half = (max_len - 5) // 2  # Reserve 5 chars for "..."
    return f"{repr_str[:half]}...{repr_str[-half:]}"


@do_wrapper
def cache(
    ttl: float | None = None,
    key_func: Callable | None = None,
    key_hashers: Mapping[str, Callable[[Any], Any] | KleisliProgram[Any, Any]] | None = None,
    *,
    lifecycle: CacheLifecycle | str | None = None,
    storage: CacheStorage | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    policy: CachePolicy | Mapping[str, Any] | None = None,
):
    """Cache decorator that uses CacheGet/CachePut effects with Safe for misses.

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
        ...     yield Tell(f"Computing result for {x}")
        ...     return x * 2
        >>> await ProgramInterpreter().run(expensive_computation(5))
        
        The first call will compute and cache the result. Subsequent calls within the TTL return
        the cached value, and the policy hints remain available to custom cache handlers.
    """
    def decorator(func: Callable[..., EffectGenerator[T]]) -> Callable[..., EffectGenerator[T]]:
        from doeff.kleisli import KleisliProgram

        if isinstance(func, KleisliProgram):
            wrapped_func = func
            signature_source = func.func
        else:
            wrapped_func = func
            signature_source = func

        func_name = _function_identifier(signature_source)
        signature = _safe_signature(signature_source)

        @do
        def run_hasher(
            hasher: Callable[[Any], Any] | KleisliProgram[Any, Any],
            value: Any,
        ) -> EffectGenerator[Any]:
            if isinstance(hasher, KleisliProgram):
                return (yield hasher(value))
            return hasher(value)

        @do
        def ensure_serializable(
            key_obj: Any,
            *,
            log_success: bool = True,
            level: str | None = None,
        ) -> EffectGenerator[None]:
            try:
                import cloudpickle

                cloudpickle.dumps(key_obj)
            except Exception as exc:  # pragma: no cover - defensive logging path
                truncated_key = _truncate_for_log(key_obj)
                yield slog(msg=f"serializing cache key failed:{truncated_key}", level="ERROR")
                raise exc

            if log_success:
                truncated_key = _truncate_for_log(key_obj)
                log_kwargs: dict[str, Any] = {"msg": f"cache key serialization check passed for key:{truncated_key}"}
                if level is not None:
                    log_kwargs["level"] = level
                yield slog(**log_kwargs)

        @do
        def build_key_inputs(
            call_args: tuple[Any, ...],
            call_kwargs: Mapping[str, Any],
        ) -> EffectGenerator[tuple[tuple[Any, ...], dict[str, Any]]]:
            if signature is None:
                kwargs_for_key = dict(call_kwargs)
                if key_hashers:
                    for name, hasher in key_hashers.items():
                        if name in kwargs_for_key:
                            kwargs_for_key[name] = yield run_hasher(hasher, kwargs_for_key[name])
                return tuple(call_args), kwargs_for_key

            try:
                bound = signature.bind(*call_args, **call_kwargs)
            except TypeError:
                kwargs_for_key = dict(call_kwargs)
                if key_hashers:
                    for name, hasher in key_hashers.items():
                        if name in kwargs_for_key:
                            kwargs_for_key[name] = yield run_hasher(hasher, kwargs_for_key[name])
                return tuple(call_args), kwargs_for_key

            bound.apply_defaults()
            arguments = bound.arguments.copy()

            kwargs_param_name: str | None = None
            for param_name, param in signature.parameters.items():
                if param.kind is inspect.Parameter.VAR_KEYWORD:
                    kwargs_param_name = param_name
                    break

            if key_hashers:
                if kwargs_param_name and kwargs_param_name in arguments:
                    arguments[kwargs_param_name] = dict(arguments[kwargs_param_name])

                for name, hasher in key_hashers.items():
                    if name in arguments:
                        arguments[name] = yield run_hasher(hasher, arguments[name])
                    elif (
                        kwargs_param_name
                        and kwargs_param_name in arguments
                        and name in arguments[kwargs_param_name]
                    ):
                        kwarg_bucket = arguments[kwargs_param_name]
                        kwarg_bucket[name] = yield run_hasher(hasher, kwarg_bucket[name])

            args_list: list[Any] = []
            kwargs_for_key: dict[str, Any] = {}

            for param_name, param in signature.parameters.items():
                value = arguments.get(param_name)
                if param.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                ):
                    args_list.append(value)
                elif param.kind is inspect.Parameter.VAR_POSITIONAL:
                    args_list.extend(value or ())
                elif param.kind is inspect.Parameter.KEYWORD_ONLY:
                    kwargs_for_key[param_name] = value
                elif param.kind is inspect.Parameter.VAR_KEYWORD and value:
                    kwargs_for_key.update(value)

            return tuple(args_list), kwargs_for_key

        @do
        def wrapper(*args, **kwargs) -> EffectGenerator[T]:
            call_site: EffectCreationContext | None = None

            call_stack = yield ProgramCallStack()
            for frame in reversed(call_stack):
                created_at = getattr(frame, "created_at", None)
                if created_at is None:
                    continue
                if not _is_internal_cache_filename(created_at.filename):
                    call_site = created_at
                    break

            if call_site is None and call_stack:
                call_site = getattr(call_stack[-1], "created_at", None)

            args_for_key, kwargs_for_key = yield build_key_inputs(tuple(args), dict(kwargs))

            frozen_kwargs = FrozenDict(kwargs_for_key) if kwargs_for_key else FrozenDict()
            cache_key_obj = (
                key_func(func_name, args_for_key, frozen_kwargs)
                if key_func
                else (func_name, args_for_key, frozen_kwargs)
            )

            yield ensure_serializable(cache_key_obj, level="DEBUG")

            @do
            def compute_and_cache() -> EffectGenerator[T]:
                yield slog(msg=f"Cache miss for {func_name}, computing...", level="DEBUG")
                program_call = wrapped_func(*args, **kwargs)
                result: Result = yield Safe(program_call)

                if result.is_ok():
                    yield ensure_serializable(cache_key_obj)
                    yield CachePut(
                        cache_key_obj,
                        result,
                        ttl,
                        lifecycle=lifecycle,
                        storage=storage,
                        metadata=metadata,
                        policy=policy,
                    )
                    yield slog(msg=f"Cache: stored result for {func_name}",level="DEBUG")
                    return result.unwrap()

                yield slog(msg=f"Computation for {func_name} failed, not caching.",level="error")
                error = result.unwrap_err()
                raise CacheComputationError(
                    func_name,
                    args,
                    dict(kwargs),
                    call_site,
                ) from error

            @do
            def try_cache_get() -> EffectGenerator[T]:
                yield ensure_serializable(cache_key_obj, log_success=False)
                return (yield CacheGet(cache_key_obj))

            cache_result = yield Safe(try_cache_get())
            if cache_result.is_ok():
                result = cache_result.value
            else:
                result = yield compute_and_cache()

            if isinstance(result, Result):
                return result.unwrap()
            return result

        try:
            wrapper.__name__ = getattr(func, "__name__", wrapper.__name__)
            wrapper.__doc__ = getattr(func, "__doc__", wrapper.__doc__)
        except (AttributeError, TypeError):
            pass

        return wrapper

    return decorator


# Environment key used by the cache handler to look up the cache path via Ask effect.
CACHE_PATH_ENV_KEY = "doeff.cache_path"


def persistent_cache_path() -> Path:
    """Return the default path used by the persistent cache handler.

    When running under the interpreter, provide `{CACHE_PATH_ENV_KEY: Path(...)}` in the
    environment to override this default. The `CacheEffectHandler` will look up this key
    at runtime.
    """
    return Path(tempfile.gettempdir()) / "doeff_cache.sqlite3"


def clear_persistent_cache(path: str | os.PathLike[str] | None = None) -> Path:
    """Clear entries from the default persistent cache and return the file path."""
    cache_path = Path(path) if path is not None else persistent_cache_path()
    if cache_path.is_dir():
        raise IsADirectoryError(f"Cache path {cache_path} is a directory")

    if not cache_path.exists():
        return cache_path

    import sqlite3

    conn = sqlite3.connect(cache_path, timeout=5)
    try:
        conn.execute("DELETE FROM cache_entries")
        conn.commit()
    except sqlite3.Error:
        conn.close()
        cache_path.unlink(missing_ok=True)
        return cache_path

    conn.close()
    return cache_path


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
    "CACHE_PATH_ENV_KEY",
    "CacheComputationError",
    "cache",
    "cache_1hour",
    "cache_1min",
    "cache_5min",
    "cache_forever",
    "cache_key",
    "clear_persistent_cache",
    "persistent_cache_path",
]
