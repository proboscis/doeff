"""Cache decorator for doeff programs using cache effects."""

import inspect
import os
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, cast

from doeff.cache_policy import CacheLifecycle, CachePolicy, CacheStorage
from doeff.decorators import do_wrapper
from doeff.do import do
from doeff.effects.cache import CacheExists, CacheGet, CachePut
from doeff.effects.execution_context import GetExecutionContext
from doeff.effects.result import Try
from doeff.effects.writer import slog
from doeff.kleisli import KleisliProgram
from doeff.types import EffectGenerator, FrozenDict


@dataclass(frozen=True)
class CacheCallSite:
    source_file: str
    source_line: int
    function_name: str

    def format_location(self) -> str:
        if self.source_file == "<rust>":
            if self.function_name != "<unknown>":
                return f"{self.function_name} (rust_builtin)"
            return "(rust_builtin)"
        if self.source_file == "<unknown>":
            if self.function_name != "<unknown>":
                return self.function_name
            return "(unknown)"
        return f"{self.source_file}:{self.source_line} in {self.function_name}"


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
    return (
        "doeff/cache.py" in normalized
        or "doeff/handlers/cache_handlers.py" in normalized
    )


def _is_hidden_cache_call_site(site: CacheCallSite) -> bool:
    if _is_internal_cache_filename(site.source_file):
        return True
    if site.function_name == "sync_spawn_intercept_handler":
        return True
    if site.source_file == "<rust>" and site.function_name != "SchedulerHandler":
        return True
    return False


def _cache_call_site_from_handler_stack_entry(entry: Any) -> CacheCallSite | None:
    if isinstance(entry, dict):
        handler_name = entry.get("handler_name")
        handler_kind = entry.get("handler_kind")
        source_file = entry.get("source_file")
        source_line = entry.get("source_line")
    else:
        try:
            handler_name = entry.handler_name
            handler_kind = entry.handler_kind
            source_file = entry.source_file
            source_line = entry.source_line
        except AttributeError:
            return None

    if not isinstance(handler_name, str):
        return None

    if source_file is None and str(handler_kind) == "rust_builtin":
        source_file = "<rust>"
    if source_line is None:
        source_line = 0

    if not isinstance(source_file, str) or not isinstance(source_line, int):
        return None

    return CacheCallSite(
        source_file=source_file,
        source_line=source_line,
        function_name=handler_name,
    )


def _call_site_from_program_frames(call_stack: list[Any] | tuple[Any, ...]) -> CacheCallSite | None:
    fallback: CacheCallSite | None = None

    for frame in reversed(call_stack):
        source_file: str | None = None
        source_line: int | None = None
        function_name: str | None = None

        if isinstance(frame, dict):
            kind = frame.get("kind")
            if kind is not None and kind != "program_yield":
                continue
            source_file = frame.get("source_file")
            source_line = frame.get("source_line")
            function_name = frame.get("function_name")
        else:
            try:
                source_file = frame.source_file
                source_line = frame.source_line
                function_name = frame.function_name
            except AttributeError:
                continue

        if not isinstance(source_file, str) or not isinstance(source_line, int):
            continue
        if not isinstance(function_name, str):
            function_name = "<unknown>"

        site = CacheCallSite(
            source_file=source_file,
            source_line=source_line,
            function_name=function_name,
        )
        if fallback is None:
            fallback = site
        if not _is_internal_cache_filename(source_file):
            return site

    return fallback


def _call_site_from_effect_entries(call_stack: list[Any] | tuple[Any, ...]) -> CacheCallSite | None:
    fallback: CacheCallSite | None = None

    for frame in reversed(call_stack):
        if isinstance(frame, dict):
            if frame.get("kind") != "effect_yield":
                continue
            handler_stack = frame.get("handler_stack", ())
        else:
            try:
                handler_stack = frame.handler_stack
            except AttributeError:
                continue

        if not isinstance(handler_stack, (list, tuple)):
            continue

        for entry in handler_stack:
            site = _cache_call_site_from_handler_stack_entry(entry)
            if site is None:
                continue
            # Spawn/Try failures can bubble out after the child task has shed its original Python
            # frames. In that path the scheduler boundary is the only stable user-visible site.
            if site.function_name == "SchedulerHandler" and site.source_file == "<rust>":
                return site
            if fallback is None and not _is_hidden_cache_call_site(site):
                fallback = site

    return fallback


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


def _cache_error_note(
    func_name: str,
    call_args: tuple[Any, ...],
    call_kwargs: dict[str, Any],
    call_site: CacheCallSite | None,
) -> str:
    location_suffix = f" at {call_site.format_location()}" if call_site is not None else ""
    return (
        f"During cache computation for {func_name}"
        f" with args={call_args!r} kwargs={call_kwargs!r}{location_suffix}"
    )


def _attach_exception_note(error: BaseException, note: str) -> None:
    if sys.version_info >= (3, 11):
        error.add_note(note)


def _call_site_from_error(error: BaseException) -> CacheCallSite | None:
    # The VM annotates surfaced exceptions with doeff_execution_context when available, so the
    # cache error path can recover call-site details without yielding inside except.
    error_obj = cast(Any, error)
    try:
        context = error_obj.doeff_execution_context
    except AttributeError:
        return None
    return _call_site_from_execution_context(context)


def _call_site_from_execution_context(context: Any) -> CacheCallSite | None:
    try:
        active_chain = context.active_chain
    except AttributeError:
        return None

    if not isinstance(active_chain, (list, tuple)):
        return None

    program_site = _call_site_from_program_frames(list(active_chain))
    if program_site is not None and not _is_hidden_cache_call_site(program_site):
        return program_site

    effect_site = _call_site_from_effect_entries(list(active_chain))
    if effect_site is not None:
        return effect_site

    return program_site


def _unwrap_cached_payload(value: Any) -> Any:
    # The VM wraps values in Ok/Err (from doeff_vm). Use duck-typing for forward compatibility.
    if hasattr(value, "is_ok") and hasattr(value, "value"):
        if callable(value.is_ok) and value.is_ok():
            return value.value
        if hasattr(value, "error"):
            raise value.error
    return value


@do_wrapper
def cache(  # noqa: PLR0915
    ttl: float | None = None,
    key_func: Callable | None = None,
    key_hashers: Mapping[str, Callable[[Any], Any] | KleisliProgram[Any, Any]] | None = None,
    *,
    lifecycle: CacheLifecycle | str | None = None,
    storage: CacheStorage | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    policy: CachePolicy | Mapping[str, Any] | None = None,
):
    """Cache decorator that uses CacheGet/CachePut effects for memoization.

    The decorator automatically caches results produced by the wrapped function. On a cache miss
    (when ``CacheGet`` raises ``KeyError``), it evaluates the original function and stores the
    successful result via ``CachePut``.

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
        >>> result = run(expensive_computation(5), handlers=default_handlers())
        >>> result.value
        10

        The first call will compute and cache the result. Subsequent calls within the TTL return
        the cached value, and the policy hints remain available to custom cache handlers.
    """

    def decorator(  # noqa: PLR0915
        func: Callable[..., EffectGenerator[T]],
    ) -> Callable[..., EffectGenerator[T]]:
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
                try:
                    import cloudpickle as serializer
                except ModuleNotFoundError:
                    import pickle as serializer

                serializer.dumps(key_obj)
            except Exception as exc:  # pragma: no cover - defensive logging path
                truncated_key = _truncate_for_log(key_obj)
                yield slog(msg=f"serializing cache key failed:{truncated_key}", level="ERROR")
                raise exc

            if log_success:
                truncated_key = _truncate_for_log(key_obj)
                log_kwargs: dict[str, Any] = {
                    "msg": f"cache key serialization check passed for key:{truncated_key}"
                }
                if level is not None:
                    log_kwargs["level"] = level
                yield slog(**log_kwargs)

        @do
        def build_key_inputs(  # noqa: PLR0912
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
                # Try keeps the exception in-band long enough to ask the VM for execution context
                # before re-raising, which preserves cache notes after the dispatch-state cleanup.
                attempt = yield Try(wrapped_func(*args, **kwargs))
                if attempt.is_err():
                    error = attempt.error
                    yield slog(msg=f"Computation for {func_name} failed, not caching.", level="error")
                    call_site = _call_site_from_error(error)
                    if call_site is None:
                        context = yield GetExecutionContext()
                        call_site = _call_site_from_execution_context(context)
                    _attach_exception_note(
                        error,
                        _cache_error_note(func_name, args, dict(kwargs), call_site),
                    )
                    raise error
                computed_value = attempt.value

                yield ensure_serializable(cache_key_obj)
                yield CachePut(
                    cache_key_obj,
                    computed_value,
                    ttl,
                    lifecycle=lifecycle,
                    storage=storage,
                    metadata=metadata,
                    policy=policy,
                )
                yield slog(msg=f"Cache: stored result for {func_name}", level="DEBUG")
                return computed_value

            @do
            def try_cache_get() -> EffectGenerator[Any]:
                yield ensure_serializable(cache_key_obj, log_success=False)
                return (yield CacheGet(cache_key_obj))

            if not (yield CacheExists(cache_key_obj)):
                return (yield compute_and_cache())

            try:
                cached_value = yield try_cache_get()
            except KeyError:
                return (yield compute_and_cache())
            return _unwrap_cached_payload(cached_value)

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
        for i in range(len(key_args)):
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
    "cache",
    "cache_1hour",
    "cache_1min",
    "cache_5min",
    "cache_forever",
    "cache_key",
    "clear_persistent_cache",
    "persistent_cache_path",
]
