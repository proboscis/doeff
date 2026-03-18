from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import doeff_vm

from doeff import (
    Ask,
    CacheExists,
    CacheGet,
    CachePut,
    Effect,
    EffectBase,
    EffectGenerator,
    Pass,
    Resume,
    WithHandler,
    cache,
    default_handlers,
    do,
    run,
)
from doeff.cache import CacheCallSite
from doeff.effects.cache import CacheExistsEffect, CacheGetEffect, CachePutEffect
from doeff.handlers import cache_handler, in_memory_cache_handler, sqlite_cache_handler
from doeff.handlers.cache_handlers import content_address, make_memo_rewriter, memo_rewriters
from doeff.storage import InMemoryStorage, SQLiteStorage
from doeff.traceback import attach_doeff_traceback


def _run_with_handlers(program: object, *handlers: object):
    wrapped = program
    for handler in handlers:
        wrapped = WithHandler(handler, wrapped)
    return run(wrapped, handlers=default_handlers())


@dataclass(frozen=True)
class MultiplyFx(EffectBase):
    value: int


class _ResultLike:
    def __init__(self, value: object) -> None:
        self.value = value

    def is_ok(self) -> bool:
        return True


class _FailingStorage:
    def get(self, key: str) -> object | None:
        return None

    def put(self, key: str, value: object) -> None:
        raise TypeError("backend cannot persist value")

    def delete(self, key: str) -> bool:
        return False

    def exists(self, key: str) -> bool:
        return False

    def keys(self) -> list[str]:
        return []

    def items(self) -> list[tuple[str, object]]:
        return []

    def clear(self) -> None:
        return None


@do
def _cache_get_program(key: object) -> EffectGenerator[object]:
    return (yield CacheGet(key))


@do
def _cache_exists_program(key: object) -> EffectGenerator[bool]:
    return (yield CacheExists(key))


@do
def _cache_put_program(key: object, value: object) -> EffectGenerator[object | None]:
    result = yield CachePut(key, value)
    return result


def test_cache_handler_get_hit() -> None:
    storage = InMemoryStorage()
    storage.put("answer", 42)

    result = _run_with_handlers(_cache_get_program("answer"), cache_handler(storage))

    assert result.is_ok(), result.error
    assert result.value == 42


def test_cache_handler_get_miss() -> None:
    result = _run_with_handlers(_cache_get_program("missing"), cache_handler(InMemoryStorage()))

    assert result.is_err()
    assert isinstance(result.error, KeyError)
    assert result.error.args == ("missing",)


def test_cache_handler_exists() -> None:
    storage = InMemoryStorage()
    storage.put("answer", 42)

    hit = _run_with_handlers(_cache_exists_program("answer"), cache_handler(storage))
    miss = _run_with_handlers(_cache_exists_program("missing"), cache_handler(storage))

    assert hit.is_ok(), hit.error
    assert miss.is_ok(), miss.error
    assert hit.value is True
    assert miss.value is False


def test_cache_handler_put() -> None:
    storage = InMemoryStorage()

    result = _run_with_handlers(_cache_put_program("answer", 42), cache_handler(storage))

    assert result.is_ok(), result.error
    assert result.value is None
    assert storage.get("answer") == 42


def test_cache_handler_put_preserves_result_like_objects_without_reinterpretation() -> None:
    storage = InMemoryStorage()
    value = _ResultLike("payload")

    result = _run_with_handlers(_cache_put_program("result-like", value), cache_handler(storage))

    assert result.is_ok(), result.error
    assert storage.get("result-like") is value


def test_cache_handler_put_preserves_vm_result_values_without_vendor_coercion() -> None:
    storage = InMemoryStorage()
    value = doeff_vm.Ok("payload")

    result = _run_with_handlers(_cache_put_program("vm-ok", value), cache_handler(storage))

    assert result.is_ok(), result.error
    stored = storage.get("vm-ok")
    assert stored is not None
    assert type(stored) is type(value)
    assert stored.value == "payload"


def test_cache_handler_put_raises_cache_boundary_error_when_storage_rejects_value() -> None:
    result = _run_with_handlers(
        _cache_put_program("bad-key", object()),
        cache_handler(_FailingStorage()),
    )

    assert result.is_err()
    assert isinstance(result.error, RuntimeError)
    assert "bad-key" in str(result.error)
    assert "persist" in str(result.error)


def test_sqlite_storage_uses_write_optimized_pragmas(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "cache.sqlite3")
    conn = storage._get_conn()

    journal_mode = conn.execute("PRAGMA journal_mode").fetchone()
    synchronous = conn.execute("PRAGMA synchronous").fetchone()
    temp_store = conn.execute("PRAGMA temp_store").fetchone()

    assert journal_mode == ("wal",)
    assert synchronous == (1,)
    assert temp_store == (2,)


def test_sqlite_storage_falls_back_to_full_sync_when_wal_is_unavailable(monkeypatch) -> None:
    class _FakeCursor:
        def __init__(self, row: tuple[object, ...] | None = None) -> None:
            self._row = row

        def fetchone(self) -> tuple[object, ...] | None:
            return self._row

    class _FakeConn:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _FakeCursor:
            self.statements.append(sql)
            if sql == "PRAGMA journal_mode=WAL":
                return _FakeCursor(("delete",))
            return _FakeCursor()

        def commit(self) -> None:
            return None

    fake_conn = _FakeConn()
    monkeypatch.setattr("doeff.storage.sqlite.sqlite3.connect", lambda _: fake_conn)

    storage = SQLiteStorage("cache.sqlite3")
    conn = storage._get_conn()

    assert conn is fake_conn
    assert "PRAGMA synchronous=FULL" in fake_conn.statements
    assert "PRAGMA synchronous=NORMAL" not in fake_conn.statements


def test_cache_decorator_with_handler(tmp_path: Path) -> None:
    calls = {"count": 0}
    db_path = tmp_path / "cache.sqlite3"

    @cache(lifecycle="persistent")
    @do
    def expensive(value: int) -> EffectGenerator[int]:
        if False:
            yield CacheGet("__typecheck__")
        calls["count"] += 1
        return value * 2

    first = _run_with_handlers(expensive(21), sqlite_cache_handler(db_path))
    second = _run_with_handlers(expensive(21), sqlite_cache_handler(db_path))

    assert first.is_ok(), first.error
    assert second.is_ok(), second.error
    assert first.value == 42
    assert second.value == 42
    assert calls["count"] == 1
    assert db_path.exists()


def test_cache_decorator_miss_then_hit() -> None:
    calls = {"count": 0}

    @cache()
    @do
    def expensive(value: int) -> EffectGenerator[int]:
        if False:
            yield CacheGet("__typecheck__")
        calls["count"] += 1
        return value * 3

    @do
    def workflow() -> EffectGenerator[tuple[int, int]]:
        first = yield expensive(7)
        second = yield expensive(7)
        return first, second

    result = _run_with_handlers(workflow(), in_memory_cache_handler())

    assert result.is_ok(), result.error
    assert result.value == (21, 21)
    assert calls["count"] == 1


def test_cache_decorator_uses_exists_fast_path_on_miss() -> None:
    calls = {"count": 0}
    storage: dict[object, object] = {}
    seen: list[str] = []

    @do
    def handler(effect: Effect, k: object):
        if isinstance(effect, CacheExistsEffect):
            seen.append("exists")
            return (yield Resume(k, effect.key in storage))
        if isinstance(effect, CacheGetEffect):
            seen.append("get")
            if effect.key not in storage:
                raise KeyError(effect.key)
            return (yield Resume(k, storage[effect.key]))
        if isinstance(effect, CachePutEffect):
            seen.append("put")
            storage[effect.key] = effect.value
            return (yield Resume(k, None))
        yield Pass()

    @cache()
    @do
    def expensive(value: int) -> EffectGenerator[int]:
        if False:
            yield CacheGet("__typecheck__")
        calls["count"] += 1
        return value * 3

    @do
    def workflow() -> EffectGenerator[tuple[int, int]]:
        first = yield expensive(7)
        second = yield expensive(7)
        return first, second

    result = _run_with_handlers(workflow(), handler)

    assert result.is_ok(), result.error
    assert result.value == (21, 21)
    assert calls["count"] == 1
    assert seen == ["exists", "put", "exists", "get"]


def test_cache_decorator_recomputes_when_cache_exists_races_with_cache_get() -> None:
    calls = {"count": 0}
    seen: list[str] = []
    storage: dict[object, object] = {}
    race_triggered = {"done": False}

    @do
    def handler(effect: Effect, k: object):
        if isinstance(effect, CacheExistsEffect):
            seen.append("exists")
            return (yield Resume(k, True))
        if isinstance(effect, CacheGetEffect):
            seen.append("get")
            if not race_triggered["done"]:
                race_triggered["done"] = True
                raise KeyError(effect.key)
            return (yield Resume(k, storage[effect.key]))
        if isinstance(effect, CachePutEffect):
            seen.append("put")
            storage[effect.key] = effect.value
            return (yield Resume(k, None))
        yield Pass()

    @cache()
    @do
    def expensive(value: int) -> EffectGenerator[int]:
        if False:
            yield CacheGet("__typecheck__")
        calls["count"] += 1
        return value * 3

    result = _run_with_handlers(expensive(7), handler)

    assert result.is_ok(), result.error
    assert result.value == 21
    assert calls["count"] == 1
    assert seen == ["exists", "get", "put"]
    assert len(storage) == 1


def test_cache_decorator_accepts_raw_cached_values() -> None:
    """After removing _CachedSuccess, raw values from handler are accepted."""

    @do
    def handler(effect: Effect, k: object):
        if isinstance(effect, CacheExistsEffect):
            return (yield Resume(k, True))
        if isinstance(effect, CacheGetEffect):
            return (yield Resume(k, 21))
        if isinstance(effect, CachePutEffect):
            raise AssertionError("unexpected cache put on forced hit")
        yield Pass()

    @cache()
    @do
    def expensive(value: int) -> EffectGenerator[int]:
        if False:
            yield CacheGet("__typecheck__")
        return value * 3

    result = _run_with_handlers(expensive(7), handler)

    assert result.is_ok(), f"Failed: {result.error}"
    assert result.value == 21


def test_cache_call_site_formats_rust_and_unknown_sentinel_locations() -> None:
    rust_site = CacheCallSite("<rust>", 0, "SchedulerHandler")
    unknown_site = CacheCallSite("<unknown>", 0, "mystery_handler")

    assert rust_site.format_location() == "SchedulerHandler (rust_builtin)"
    assert unknown_site.format_location() == "mystery_handler"


def test_cache_decorator_reraises_original_error_with_cache_note() -> None:
    @cache()
    @do
    def expensive() -> EffectGenerator[int]:
        _ = yield Ask("cache_missing_key")
        return 1

    result = run(
        WithHandler(in_memory_cache_handler(), expensive()),
        handlers=default_handlers(),
        print_doeff_trace=False,
    )

    assert result.is_err()
    error = result.error
    assert type(error).__name__ == "MissingEnvKeyError"

    notes = getattr(error, "__notes__", ())
    if sys.version_info >= (3, 11):
        assert any("During cache computation for" in note for note in notes)
        assert any("SchedulerHandler (rust_builtin)" in note for note in notes)

    doeff_tb = attach_doeff_traceback(error, traceback_data=result.traceback_data)
    assert doeff_tb is not None

    rendered = doeff_tb.format_default()
    assert "MissingEnvKeyError" in rendered
    assert "cache_missing_key" in rendered


def test_in_memory_cache_handler() -> None:
    @do
    def workflow() -> EffectGenerator[str]:
        _ = yield CachePut("alpha", "beta")
        return (yield CacheGet("alpha"))

    result = _run_with_handlers(workflow(), in_memory_cache_handler())

    assert result.is_ok(), result.error
    assert result.value == "beta"


def test_memo_rewriter() -> None:
    calls = {"count": 0}
    rewriter = make_memo_rewriter(MultiplyFx)
    [batch_rewriter] = memo_rewriters(MultiplyFx)

    @do
    def multiply_handler(effect: Effect, k: object):
        if not isinstance(effect, MultiplyFx):
            yield Pass()
            return
        calls["count"] += 1
        return (yield Resume(k, effect.value * 5))

    @do
    def workflow() -> EffectGenerator[tuple[int, int]]:
        first = yield MultiplyFx(3)
        second = yield MultiplyFx(3)
        return first, second

    result = _run_with_handlers(
        workflow(),
        rewriter,
        in_memory_cache_handler(),
        batch_rewriter,
        multiply_handler,
    )

    assert result.is_ok(), result.error
    assert result.value == (15, 15)
    assert calls["count"] == 1
    assert content_address(MultiplyFx(3)) == content_address(MultiplyFx(3))
