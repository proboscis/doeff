from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from doeff import (
    Ask,
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
from doeff.cache import CacheCallSite, CacheComputationError
from doeff.handlers import cache_handler, in_memory_cache_handler, sqlite_cache_handler
from doeff.handlers.cache_handlers import content_address, make_memo_rewriter, memo_rewriters
from doeff.storage import InMemoryStorage
from doeff.traceback import attach_doeff_traceback


def _run_with_handlers(program: object, *handlers: object):
    wrapped = program
    for handler in handlers:
        wrapped = WithHandler(handler, wrapped)
    return run(wrapped, handlers=default_handlers())


@dataclass(frozen=True)
class MultiplyFx(EffectBase):
    value: int


@do
def _cache_get_program(key: object) -> EffectGenerator[object]:
    return (yield CacheGet(key))


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


def test_cache_handler_put() -> None:
    storage = InMemoryStorage()

    result = _run_with_handlers(_cache_put_program("answer", 42), cache_handler(storage))

    assert result.is_ok(), result.error
    assert result.value is None
    assert storage.get("answer") == 42


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


def test_cache_computation_error_hides_rust_and_unknown_sentinel_locations() -> None:
    rust_error = CacheComputationError(
        "expensive",
        (),
        {},
        CacheCallSite("<rust>", 0, "SchedulerHandler"),
    )
    unknown_error = CacheComputationError(
        "expensive",
        (),
        {},
        CacheCallSite("<unknown>", 0, "mystery_handler"),
    )

    assert "<rust>:0" not in str(rust_error)
    assert "<unknown>" not in str(unknown_error)
    assert "(rust_builtin)" in str(rust_error)
    assert ":0" not in str(unknown_error)


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
    assert not isinstance(error, CacheComputationError)

    notes = getattr(error, "__notes__", ())
    if hasattr(error, "add_note"):
        assert any("During cache computation for" in note for note in notes)

    doeff_tb = attach_doeff_traceback(error, traceback_data=result.traceback_data)
    assert doeff_tb is not None

    rendered = doeff_tb.format_default()
    assert "MissingEnvKeyError" in rendered
    assert "CacheComputationError" not in rendered
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
