"""Cache effect handlers and memoization helpers."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Callable, Mapping, Set
from pathlib import Path
from typing import Any

import doeff_vm

from doeff._vendor import Err as PyErr
from doeff._vendor import Ok as PyOk
from doeff.do import do
from doeff.effects.cache import CacheGet, CacheGetEffect, CachePut, CachePutEffect
from doeff.effects.result import Try
from doeff.storage import DurableStorage, InMemoryStorage, SQLiteStorage
from doeff.types import Effect


def _dumps(value: Any) -> bytes:
    try:
        import cloudpickle as serializer
    except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
        import pickle as serializer

    return serializer.dumps(value)


def _normalize_for_hash(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        normalized: Any = {
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


def _storage_key(key: Any) -> str:
    if isinstance(key, str):
        return key
    return f"sha256:{content_address(key)}"


def _stored_value(value: Any) -> Any:
    if (
        hasattr(value, "is_ok")
        and callable(value.is_ok)
        and value.is_ok()
        and hasattr(value, "value")
    ):
        return PyOk(value.value)

    if (
        hasattr(value, "is_err")
        and callable(value.is_err)
        and value.is_err()
        and hasattr(value, "error")
    ):
        return PyErr(value.error)

    return value


def content_address(effect: Any) -> str:
    """Return a SHA-256 content address for an effect payload."""

    try:
        payload = _dumps(effect)
    except Exception:
        payload = json.dumps(_normalize_for_hash(effect), sort_keys=True, default=repr).encode(
            "utf-8"
        )
    return hashlib.sha256(payload).hexdigest()


def cache_handler(storage: DurableStorage) -> Any:
    """Interpret CacheGet/CachePut against a pluggable storage backend."""

    @do
    def handler(effect: CacheGetEffect | CachePutEffect, k: Any):
        if not isinstance(effect, (CacheGetEffect, CachePutEffect)):
            yield doeff_vm.Pass()
            return

        key = _storage_key(effect.key)

        if isinstance(effect, CacheGetEffect):
            value = storage.get(key)
            if value is None and not storage.exists(key):
                raise KeyError(effect.key)
            return (yield doeff_vm.Resume(k, value))

        storage.put(key, _stored_value(effect.value))
        return (yield doeff_vm.Resume(k, None))

    return handler


def in_memory_cache_handler() -> Any:
    """Return a cache handler backed by in-memory storage."""

    return cache_handler(InMemoryStorage())


def sqlite_cache_handler(db_path: str | Path) -> Any:
    """Return a cache handler backed by SQLite storage."""

    return cache_handler(SQLiteStorage(db_path))


def make_memo_rewriter(
    effect_type: type[Any],
    key_fn: Callable[[Any], str] = content_address,
) -> Any:
    """Create an interceptor that memoizes handled effects through CacheGet/CachePut."""

    @do
    def handler(effect: Effect, k: Any):
        if not isinstance(effect, effect_type):
            yield doeff_vm.Pass()
            return

        key = key_fn(effect)

        @do
        def cache_lookup():
            return (yield CacheGet(key))

        cached = yield Try(cache_lookup())
        if cached.is_ok():
            return (yield doeff_vm.Resume(k, cached.value))

        if not isinstance(cached.error, KeyError):
            raise cached.error

        delegated = yield doeff_vm.Delegate()
        _ = yield CachePut(key, delegated)
        return (yield doeff_vm.Resume(k, delegated))

    return handler


def memo_rewriters(
    *effect_types: type[Any],
    key_fn: Callable[[Any], str] = content_address,
) -> list[Any]:
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
