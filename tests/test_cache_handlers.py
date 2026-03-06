from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff import (
    CacheGet,
    CachePut,
    EffectBase,
    EffectGenerator,
    Resume,
    Try,
    WithHandler,
    cache,
    content_address,
    default_handlers,
    do,
    make_memo_rewriter,
    memo_rewriters,
    run,
)
from doeff.handlers import cache_handler, in_memory_cache_handler, sqlite_cache_handler
from doeff.storage import InMemoryStorage


def _is_ok(result: Any) -> bool:
    probe = getattr(result, "is_ok", None)
    if callable(probe):
        return bool(probe())
    return bool(probe)


def _with_handlers(program: Any, *handlers: Any) -> Any:
    wrapped = program
    for handler in handlers:
        wrapped = WithHandler(handler, wrapped)
    return wrapped


@dataclass(frozen=True)
class Lookup(EffectBase):
    query: str


def test_cache_handler_get_hit() -> None:
    storage = InMemoryStorage()
    storage.put(content_address("hit"), {"value": 1})

    @do
    def program() -> EffectGenerator[dict[str, int]]:
        return (yield CacheGet("hit"))

    result = run(WithHandler(cache_handler(storage), program()), handlers=default_handlers())

    assert _is_ok(result), result.error
    assert result.value == {"value": 1}


def test_cache_handler_get_miss() -> None:
    storage = InMemoryStorage()

    @do
    def program() -> EffectGenerator[Any]:
        return (yield Try(CacheGet("missing")))

    result = run(WithHandler(cache_handler(storage), program()), handlers=default_handlers())

    assert _is_ok(result), result.error
    assert result.value.is_err()
    assert isinstance(result.value.error, KeyError)
    assert result.value.error.args == ("missing",)


def test_cache_handler_put() -> None:
    storage = InMemoryStorage()

    @do
    def program() -> EffectGenerator[None]:
        return (yield CachePut("put-key", "stored"))

    result = run(WithHandler(cache_handler(storage), program()), handlers=default_handlers())

    assert _is_ok(result), result.error
    assert result.value is None
    assert storage.get(content_address("put-key")) == "stored"


def test_in_memory_cache_handler() -> None:
    handler = in_memory_cache_handler()

    @do
    def program() -> EffectGenerator[str]:
        _ = yield CachePut("cycle", "value")
        return (yield CacheGet("cycle"))

    result = run(WithHandler(handler, program()), handlers=default_handlers())

    assert _is_ok(result), result.error
    assert result.value == "value"


def test_cache_decorator_miss_then_hit() -> None:
    calls = {"count": 0}
    handler = in_memory_cache_handler()

    @cache()
    @do
    def expensive(value: int) -> EffectGenerator[int]:
        calls["count"] += 1
        return value * 2

    first = run(WithHandler(handler, expensive(21)), handlers=default_handlers())
    second = run(WithHandler(handler, expensive(21)), handlers=default_handlers())

    assert _is_ok(first), first.error
    assert _is_ok(second), second.error
    assert first.value == 42
    assert second.value == 42
    assert calls["count"] == 1


def test_cache_decorator_with_handler(tmp_path) -> None:
    calls = {"count": 0}
    db_path = tmp_path / "cache.sqlite3"

    @cache(lifecycle="persistent")
    @do
    def expensive(value: int) -> EffectGenerator[int]:
        calls["count"] += 1
        return value + 1

    first = run(WithHandler(sqlite_cache_handler(db_path), expensive(10)), handlers=default_handlers())
    second = run(WithHandler(sqlite_cache_handler(db_path), expensive(10)), handlers=default_handlers())

    assert _is_ok(first), first.error
    assert _is_ok(second), second.error
    assert first.value == 11
    assert second.value == 11
    assert calls["count"] == 1
    assert db_path.exists()


def test_memo_rewriter() -> None:
    calls = {"count": 0}
    assert len(memo_rewriters(Lookup)) == 1
    memo = make_memo_rewriter(Lookup, key_fn=content_address)

    @do
    def lookup_source(effect: Lookup, k: object):
        calls["count"] += 1
        return (yield Resume(k, effect.query.upper()))

    @do
    def program() -> EffectGenerator[tuple[str, str]]:
        first = yield Lookup("alpha")
        second = yield Lookup("alpha")
        return first, second

    result = run(
        _with_handlers(program(), memo, in_memory_cache_handler(), lookup_source),
        handlers=default_handlers(),
    )

    assert _is_ok(result), result.error
    assert result.value == ("ALPHA", "ALPHA")
    assert calls["count"] == 1
