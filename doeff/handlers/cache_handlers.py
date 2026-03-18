"""Cache effect handlers and memoization helpers."""

import dataclasses
import hashlib
import json
from collections.abc import Callable, Mapping, Set
from pathlib import Path
from typing import Any, TypeAlias, cast

import doeff_vm

from doeff.do import do
from doeff.effects.cache import (
    CacheExistsEffect,
    CacheGet,
    CacheGetEffect,
    CachePut,
    CachePutEffect,
)
from doeff.effects.result import Try
from doeff.storage import DurableStorage, InMemoryStorage, SQLiteStorage
from doeff.types import Effect

MemoKeyFn: TypeAlias = Callable[[object], str]
CacheProtocolHandler: TypeAlias = Callable[..., object]


def _dumps(value: object) -> bytes:
    try:
        import cloudpickle as serializer
    except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
        import pickle as serializer

    return serializer.dumps(value)


def _normalize_for_hash(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        normalized: object = {
            "__type__": f"{type(value).__module__}.{type(value).__qualname__}",
            **{
                field.name: _normalize_for_hash(getattr(value, field.name))
                for field in dataclasses.fields(value)
            },
        }
    elif isinstance(value, Mapping):
        normalized = {
            str(key): _normalize_for_hash(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    elif isinstance(value, tuple):
        normalized = {"__tuple__": [_normalize_for_hash(item) for item in value]}
    elif isinstance(value, list):
        normalized = [_normalize_for_hash(item) for item in value]
    elif isinstance(value, Set) and not isinstance(value, (str, bytes, bytearray)):
        normalized = [_normalize_for_hash(item) for item in value]
        normalized = {
            "__set__": sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))
        }
    elif isinstance(value, Path):
        normalized = {"__path__": str(value)}
    elif isinstance(value, bytes):
        normalized = {"__bytes__": value.hex()}
    elif isinstance(value, type):
        normalized = f"{value.__module__}.{value.__qualname__}"
    else:
        normalized = value

    return normalized


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


def cache_handler(storage: DurableStorage) -> CacheProtocolHandler:
    """Interpret CacheGet/CachePut against a pluggable storage backend."""

    @do
    def handler(effect: CacheGetEffect | CacheExistsEffect | CachePutEffect, k: object):
        if not isinstance(effect, (CacheGetEffect, CacheExistsEffect, CachePutEffect)):
            yield doeff_vm.Pass()
            return None

        key = _storage_key(effect.key)

        if isinstance(effect, CacheExistsEffect):
            return (yield doeff_vm.Resume(cast(Any, k), storage.exists(key)))

        if isinstance(effect, CacheGetEffect):
            value = storage.get(key)
            if value is None and not storage.exists(key):
                raise KeyError(effect.key)
            return (yield doeff_vm.Resume(cast(Any, k), value))

        _persist_value(storage, key, original_key=effect.key, value=effect.value)
        return (yield doeff_vm.Resume(cast(Any, k), None))

    return handler


def in_memory_cache_handler() -> CacheProtocolHandler:
    """Return a cache handler backed by in-memory storage."""

    return cache_handler(InMemoryStorage())


def sqlite_cache_handler(db_path: str | Path) -> CacheProtocolHandler:
    """Return a cache handler backed by SQLite storage."""

    return cache_handler(SQLiteStorage(db_path))


def make_memo_rewriter(
    effect_type: type[object],
    key_fn: MemoKeyFn = content_address,
) -> CacheProtocolHandler:
    """Create an interceptor that memoizes handled effects through CacheGet/CachePut."""

    @do
    def handler(effect: Effect, k: object):
        if not isinstance(effect, effect_type):
            yield doeff_vm.Pass()
            return None

        key = key_fn(effect)

        @do
        def cache_lookup():
            return (yield CacheGet(key))

        cached = yield Try(cache_lookup())
        if cached.is_ok():
            return (yield doeff_vm.Resume(cast(Any, k), cached.value))

        if not isinstance(cached.error, KeyError):
            raise cached.error

        delegated = yield doeff_vm.Delegate()
        _ = yield CachePut(key, delegated)
        return (yield doeff_vm.Resume(cast(Any, k), delegated))

    return handler


def memo_rewriters(
    *effect_types: type[object],
    key_fn: MemoKeyFn = content_address,
) -> list[CacheProtocolHandler]:
    """Create memo rewriters for each provided effect type."""

    return [make_memo_rewriter(effect_type, key_fn=key_fn) for effect_type in effect_types]


__all__ = [
    "cache_handler",
    "content_address",
    "in_memory_cache_handler",
    "make_memo_rewriter",
    "memo_rewriters",
    "sqlite_cache_handler",
]
