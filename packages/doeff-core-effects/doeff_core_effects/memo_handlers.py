"""Memo effect handlers and memoization helpers.

Replaces the old cache_handlers module with cost-aware routing:
- CHEAP effects → ephemeral storage (Redis, in-memory)
- EXPENSIVE/IRREPRODUCIBLE effects → durable storage (MinIO, dedicated SQLite)
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Callable, Mapping, Set
from pathlib import Path
from typing import Any, TypeAlias

from doeff import do
from doeff.program import Resume, Pass

from doeff_core_effects.memo_effects import (
    MemoExistsEffect,
    MemoGetEffect,
    MemoPutEffect,
    MemoGet,
    MemoPut,
    MemoExists,
)
from doeff_core_effects.memo_policy import RecomputeCost
from doeff_core_effects.effects import Try
from doeff_core_effects.storage import DurableStorage, InMemoryStorage, SQLiteStorage

MemoKeyFn: TypeAlias = Callable[[object], str]


def json_gzip_serialize(value: Any) -> bytes:
    """Serialize value to gzip JSON. Returns bytes (pure, not effectful)."""
    import gzip
    import time as _time

    def _default(obj):
        if hasattr(obj, "__dict__") and not isinstance(obj, type):
            return {"__type__": f"{type(obj).__module__}.{type(obj).__qualname__}", **obj.__dict__}
        return repr(obj)

    payload = {"response": value, "stored_at": _time.time()}
    json_bytes = json.dumps(payload, default=_default, ensure_ascii=False).encode("utf-8")
    return gzip.compress(json_bytes)


def json_gzip_deserialize(data: bytes) -> Any:
    """Deserialize gzip JSON bytes. Returns value (pure, not effectful)."""
    import gzip
    return json.loads(gzip.decompress(data))["response"]


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


def content_address(effect: object) -> str:
    """Return a SHA-256 content address for an effect payload."""
    try:
        payload = _dumps(effect)
    except Exception:
        payload = json.dumps(_normalize_for_hash(effect), sort_keys=True, default=repr).encode(
            "utf-8"
        )
    return hashlib.sha256(payload).hexdigest()


def _matches_cost(effect_cost: RecomputeCost, handler_cost: RecomputeCost | None) -> bool:
    """Check if an effect's cost matches the handler's cost filter."""
    if handler_cost is None:
        return True
    if handler_cost == RecomputeCost.EXPENSIVE:
        return effect_cost in (RecomputeCost.EXPENSIVE, RecomputeCost.IRREPRODUCIBLE)
    return effect_cost == handler_cost


def _effect_cost(effect) -> RecomputeCost:
    """Extract recompute_cost from a memo effect."""
    if isinstance(effect, MemoPutEffect):
        return effect.policy.recompute_cost
    return effect.recompute_cost


def memo_handler(
    storage: DurableStorage,
    *,
    cost: RecomputeCost | str | None = None,
    name: str | None = None,
):
    """Handle memo effects as a caching proxy.

    On hit: Resume with stored value.
    On miss: re-perform (delegates to outer handler) → cache result → Resume.

    Stack multiple handlers for layered caching:

        WithHandler(memo_handler(minio, cost=EXPENSIVE, name="minio"),
          WithHandler(memo_handler(redis, cost=CHEAP, name="redis"),
            WithHandler(memo_handler(memory, name="L1"),
              program)))

    Each handler is a caching proxy. Position determines terminal behavior:
    the outermost handler's miss re-perform is unhandled → memo_rewriter treats as miss.

    Args:
        storage: The storage backend for this handler.
        cost: Only handle effects matching this cost tier. None = handle all.
        name: Label for log messages (defaults to storage class name).
    """
    if isinstance(cost, str):
        cost = RecomputeCost(cost)

    label = name or type(storage).__name__

    from doeff_core_effects.effects import WriterTellEffect as Slog

    @do
    def handler(effect, k):
        if not isinstance(effect, (MemoGetEffect, MemoExistsEffect, MemoPutEffect)):
            yield Pass(effect, k)
            return

        if not _matches_cost(_effect_cost(effect), cost):
            yield Pass(effect, k)
            return

        key = _storage_key(effect.key)

        if isinstance(effect, MemoExistsEffect):
            exists = yield storage.exists(key)
            if exists:
                result = yield Resume(k, True)
                return result
            # Not in this layer — re-perform to check outer layers
            result = yield Resume(k, (yield effect))
            return result

        if isinstance(effect, MemoGetEffect):
            exists = yield storage.exists(key)
            if exists:
                value = yield storage.get(key)
                yield Slog(f"[memo-layer:{label}] HIT key={key[:16]}...")
                result = yield Resume(k, value)
                return result
            # Miss — re-perform (outer handler resolves) → cache result
            yield Slog(f"[memo-layer:{label}] MISS key={key[:16]}... → re-performing")
            outer_value = yield effect
            yield storage.put(key, outer_value)
            yield Slog(f"[memo-layer:{label}] WRITE-THROUGH key={key[:16]}...")
            result = yield Resume(k, outer_value)
            return result

        # MemoPutEffect — write to this layer AND propagate to outer layers
        yield storage.put(key, effect.value)
        yield Slog(f"[memo-layer:{label}] PUT key={key[:16]}...")
        yield effect  # re-perform → outer handlers also store
        result = yield Resume(k, None)
        return result

    return handler


@do
def memo_terminal(effect, k):
    """Terminal handler for memo effects — sits outside all memo_handler layers.

    Catches re-performed memo effects that fall through every caching layer:
      MemoExists → False (not in any layer)
      MemoGet    → KeyError (propagates to memo_rewriter's except KeyError)
      MemoPut    → None (all layers already stored before re-performing)
    """
    if isinstance(effect, MemoExistsEffect):
        result = yield Resume(k, False)
        return result
    if isinstance(effect, MemoGetEffect):
        raise KeyError(effect.key)
    if isinstance(effect, MemoPutEffect):
        result = yield Resume(k, None)
        return result
    yield Pass(effect, k)


def in_memory_memo_handler():
    """Return a memo handler backed by in-memory storage (handles all costs)."""
    return memo_handler(InMemoryStorage())


def sqlite_memo_handler(db_path: str | Path):
    """Return a memo handler backed by SQLite storage (handles all costs)."""
    return memo_handler(SQLiteStorage(db_path))



def make_memo_rewriter(
    effect_type: type,
    key_fn: MemoKeyFn = content_address,
    recompute_cost: RecomputeCost | str = RecomputeCost.CHEAP,
):
    """Create a handler that memoizes effects of the given type through memo storage.

    On memo hit: Resume with stored value (outer handler not called).
    On memo miss: re-perform effect -> outer handler handles it -> store result.

    Serialization is NOT handled here — use a separate pydantic_serialize_handler
    in the handler stack for that.

    Args:
        effect_type: The effect class to memoize.
        key_fn: Function to compute storage key from effect.
        recompute_cost: Cost tier for routing (CHEAP -> ephemeral, EXPENSIVE -> durable).
    """
    from doeff_core_effects.effects import WriterTellEffect as Slog
    from doeff_core_effects.memo_policy import MemoPolicy

    if isinstance(recompute_cost, str):
        recompute_cost = RecomputeCost(recompute_cost)

    @do
    def handler(effect, k):
        if not isinstance(effect, effect_type):
            yield Pass(effect, k)
            return

        key = key_fn(effect)
        yield Slog(f"[memo] checking {effect_type.__name__} key={key[:16]}...")

        _MISS = object()
        if (yield MemoExists(key, recompute_cost=recompute_cost)):
            try:
                cached = yield MemoGet(key, recompute_cost=recompute_cost)
            except KeyError:
                cached = _MISS

            if cached is not _MISS:
                yield Slog(f"[memo] HIT {effect_type.__name__} key={key[:16]}...")
                result = yield Resume(k, cached)
                return result

        yield Slog(f"[memo] MISS {effect_type.__name__} key={key[:16]}... -> delegating")
        delegated = yield effect

        yield MemoPut(key, delegated, policy=MemoPolicy(recompute_cost=recompute_cost), source_effect=effect)
        yield Slog(f"[memo] STORED {effect_type.__name__} key={key[:16]}...")

        result = yield Resume(k, delegated)
        return result

    return handler


def memo_rewriters(
    *effect_types: type,
    key_fn: MemoKeyFn = content_address,
    recompute_cost: RecomputeCost | str = RecomputeCost.CHEAP,
) -> list:
    """Create memo rewriter handlers for each provided effect type.

    Args:
        effect_types: Effect classes to memoize.
        key_fn: Function to compute storage key from effect.
        recompute_cost: Cost tier for all provided types.
    """
    return [make_memo_rewriter(et, key_fn=key_fn, recompute_cost=recompute_cost) for et in effect_types]
