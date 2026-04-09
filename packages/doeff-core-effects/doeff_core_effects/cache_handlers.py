"""Cache effect handlers and memoization helpers."""

import dataclasses
import hashlib
import json
from collections.abc import Callable, Mapping, Set
from pathlib import Path
from typing import Any, TypeAlias

from doeff import do
from doeff.program import Resume, Pass

from doeff_core_effects.cache_effects import (
    CacheExistsEffect,
    CacheGetEffect,
    CachePutEffect,
    CacheGet,
    CachePut,
    CacheExists,
)
from doeff_core_effects.effects import Try
from doeff_core_effects.storage import DurableStorage, InMemoryStorage, SQLiteStorage

MemoKeyFn: TypeAlias = Callable[[object], str]


def _dumps(value: object) -> bytes:
    try:
        import cloudpickle as serializer
    except ModuleNotFoundError:
        import pickle as serializer

    return serializer.dumps(value)


def _normalize_for_hash(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "__type__": f"{type(value).__module__}.{type(value).__qualname__}",
            **{
                field.name: _normalize_for_hash(getattr(value, field.name))
                for field in dataclasses.fields(value)
            },
        }
    elif isinstance(value, Mapping):
        return {
            str(key): _normalize_for_hash(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    elif isinstance(value, tuple):
        return {"__tuple__": [_normalize_for_hash(item) for item in value]}
    elif isinstance(value, list):
        return [_normalize_for_hash(item) for item in value]
    elif isinstance(value, Set) and not isinstance(value, (str, bytes, bytearray)):
        normalized = [_normalize_for_hash(item) for item in value]
        return {
            "__set__": sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))
        }
    elif isinstance(value, Path):
        return {"__path__": str(value)}
    elif isinstance(value, bytes):
        return {"__bytes__": value.hex()}
    elif isinstance(value, type):
        return f"{value.__module__}.{value.__qualname__}"
    elif hasattr(value, "__dict__") and not isinstance(value, type):
        # Generic object with __dict__ (e.g. EffectBase subclasses)
        return {
            "__type__": f"{type(value).__module__}.{type(value).__qualname__}",
            **{
                k: _normalize_for_hash(v)
                for k, v in sorted(value.__dict__.items())
            },
        }
    return value


def _storage_key(key: object) -> str:
    if isinstance(key, str):
        return key
    return f"sha256:{content_address(key)}"


def _persist_value(
    storage: DurableStorage,
    storage_key: str,
    *,
    original_key: object,
    value: object,
) -> None:
    try:
        storage.put(storage_key, value)
    except Exception as exc:
        raise RuntimeError(
            f"Cache handler could not persist value for key {original_key!r}: {exc}"
        ) from exc


def content_address(effect: object) -> str:
    """Return a SHA-256 content address for an effect payload."""
    try:
        payload = _dumps(effect)
    except Exception:
        payload = json.dumps(_normalize_for_hash(effect), sort_keys=True, default=repr).encode(
            "utf-8"
        )
    return hashlib.sha256(payload).hexdigest()


def cache_handler(storage: DurableStorage):
    """Interpret CacheGet/CachePut/CacheExists against a pluggable storage backend.

    Storage methods return Programs (DoExpr). The handler yields them to get values.
    This makes the handler transparent to sync (Pure) and async (Await) storage alike.
    """

    @do
    def handler(effect, k):
        if not isinstance(effect, (CacheGetEffect, CacheExistsEffect, CachePutEffect)):
            yield Pass(effect, k)
            return

        key = _storage_key(effect.key)

        if isinstance(effect, CacheExistsEffect):
            exists = yield storage.exists(key)
            result = yield Resume(k, exists)
            return result

        if isinstance(effect, CacheGetEffect):
            value = yield storage.get(key)
            if value is None:
                exists = yield storage.exists(key)
                if not exists:
                    from doeff.program import ResumeThrow
                    return (yield ResumeThrow(k, KeyError(effect.key)))
            result = yield Resume(k, value)
            return result

        # CachePutEffect
        yield storage.put(key, effect.value)
        result = yield Resume(k, None)
        return result

    return handler


def in_memory_cache_handler():
    """Return a cache handler backed by in-memory storage."""
    return cache_handler(InMemoryStorage())


def sqlite_cache_handler(db_path: str | Path):
    """Return a cache handler backed by SQLite storage."""
    return cache_handler(SQLiteStorage(db_path))


def make_memo_rewriter(
    effect_type: type,
    key_fn: MemoKeyFn = content_address,
):
    """Create a handler that memoizes effects of the given type through cache.

    On cache hit: Resume with cached value (outer handler not called).
    On cache miss: re-perform effect → outer handler handles it → store result in cache.
    """
    from doeff_core_effects.effects import WriterTellEffect as Slog

    @do
    def handler(effect, k):
        if not isinstance(effect, effect_type):
            yield Pass(effect, k)
            return

        key = key_fn(effect)
        yield Slog(f"[memo] checking {effect_type.__name__} key={key[:16]}…")

        # Check cache — narrow try/except to CacheGet only.
        # Resume(k) must NOT be inside try/except: if the resumed body raises
        # KeyError, it must propagate, not fall through to the MISS path.
        _MISS = object()  # sentinel
        if (yield CacheExists(key)):
            try:
                cached = yield CacheGet(key)
            except KeyError:
                cached = _MISS  # race: evicted between Exists and Get

            if cached is not _MISS:
                yield Slog(f"[memo] HIT {effect_type.__name__} key={key[:16]}…")
                result = yield Resume(k, cached)
                return result

        # Cache miss — "delegate" by re-performing the effect.
        yield Slog(f"[memo] MISS {effect_type.__name__} key={key[:16]}… → delegating")
        delegated = yield effect

        # Store in cache
        yield CachePut(key, delegated)
        yield Slog(f"[memo] STORED {effect_type.__name__} key={key[:16]}…")

        # Resume body with result
        result = yield Resume(k, delegated)
        return result

    return handler


def memo_rewriters(
    *effect_types: type,
    key_fn: MemoKeyFn = content_address,
) -> list:
    """Create memo rewriter handlers for each provided effect type."""
    return [make_memo_rewriter(et, key_fn=key_fn) for et in effect_types]


__all__ = [
    "cache_handler",
    "content_address",
    "in_memory_cache_handler",
    "make_memo_rewriter",
    "memo_rewriters",
    "sqlite_cache_handler",
]
