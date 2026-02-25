from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff import (
    CacheGet,
    CachePut,
    Delegate,
    EffectGenerator,
    Pass,
    Resume,
    WithHandler,
    default_handlers,
    do,
    run,
)
from doeff.effects.base import EffectBase
from doeff.effects.cache import CacheGetEffect, CachePutEffect


def _with_handlers(program: Any, *handlers: Any) -> Any:
    wrapped = program
    for handler in handlers:
        wrapped = WithHandler(handler, wrapped)
    return wrapped


def _is_ok(result: Any) -> bool:
    probe = getattr(result, "is_ok", None)
    if callable(probe):
        return bool(probe())
    return bool(probe)


def _is_err(result: Any) -> bool:
    probe = getattr(result, "is_err", None)
    if callable(probe):
        return bool(probe())
    return bool(probe)


@dataclass(frozen=True, kw_only=True)
class Greet(EffectBase):
    name: str


@dataclass(frozen=True, kw_only=True)
class Noop(EffectBase):
    tag: str = "noop"


def greet_handler(effect: object, k: object):
    if not isinstance(effect, Greet):
        yield Pass()
        return
    return (yield Resume(k, f"hello {effect.name}"))


def noop_handler(effect: object, k: object):
    if not isinstance(effect, Noop):
        yield Pass()
        return
    return (yield Resume(k, effect.tag))


@do
def greet_world() -> EffectGenerator[str]:
    return (yield Greet(name="world"))


def test_effect_yield_then_delegate_basic() -> None:
    def interceptor(effect: object, k: object):
        if not isinstance(effect, Greet):
            yield Pass()
            return
        _ = yield Noop()
        delegated = yield Delegate()
        return (yield Resume(k, delegated))

    wrapped = _with_handlers(greet_world(), interceptor, noop_handler, greet_handler)
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    assert result.value == "hello world"


def test_effect_yield_then_delegate_with_multiple_nested_effects() -> None:
    def interceptor(effect: object, k: object):
        if not isinstance(effect, Greet):
            yield Pass()
            return
        first = yield Noop(tag="first")
        second = yield Noop(tag="second")
        delegated = yield Delegate()
        return (yield Resume(k, f"{first}|{second}|{delegated}"))

    wrapped = _with_handlers(greet_world(), interceptor, noop_handler, greet_handler)
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    assert result.value == "first|second|hello world"


def test_cache_miss_delegate_then_put_then_hit() -> None:
    cache_store: dict[object, object] = {}
    delegated_calls = {"count": 0}

    def cache_backend(effect: object, k: object):
        if isinstance(effect, CacheGetEffect):
            return (yield Resume(k, cache_store.get(effect.key)))
        if isinstance(effect, CachePutEffect):
            cache_store[effect.key] = effect.value
            return (yield Resume(k, effect.value))
        yield Pass()

    def greet_source(effect: object, k: object):
        if not isinstance(effect, Greet):
            yield Pass()
            return
        delegated_calls["count"] += 1
        return (yield Resume(k, f"hello {effect.name}"))

    def cache_interceptor(effect: object, k: object):
        if not isinstance(effect, Greet):
            yield Pass()
            return
        key = ("greet", effect.name)
        cached = yield CacheGet(key)
        if cached is None:
            delegated = yield Delegate()
            _ = yield CachePut(key, delegated)
            return (yield Resume(k, delegated))
        return (yield Resume(k, cached))

    @do
    def run_twice() -> EffectGenerator[tuple[str, str]]:
        first = yield Greet(name="cached")
        second = yield Greet(name="cached")
        return first, second

    wrapped = _with_handlers(run_twice(), cache_interceptor, cache_backend, greet_source)
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    assert result.value == ("hello cached", "hello cached")
    assert delegated_calls["count"] == 1
    assert ("greet", "cached") in cache_store


def test_nested_interceptors_preserve_outer_dispatch_context() -> None:
    seen: list[str] = []

    def inner_interceptor(effect: object, k: object):
        if not isinstance(effect, Greet):
            yield Pass()
            return
        seen.append("inner")
        _ = yield Noop(tag="inner-pre")
        delegated = yield Delegate()
        return (yield Resume(k, f"{delegated}|inner"))

    def outer_interceptor(effect: object, k: object):
        if not isinstance(effect, Greet):
            yield Pass()
            return
        seen.append("outer")
        _ = yield Noop(tag="outer-pre")
        delegated = yield Delegate()
        return (yield Resume(k, f"{delegated}|outer"))

    wrapped = _with_handlers(
        greet_world(),
        inner_interceptor,
        outer_interceptor,
        noop_handler,
        greet_handler,
    )
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    assert result.value == "hello world|outer|inner"
    assert seen == ["inner", "outer"]


def test_delegate_without_prior_effect_yield_regression() -> None:
    def interceptor(effect: object, k: object):
        if not isinstance(effect, Greet):
            yield Pass()
            return
        delegated = yield Delegate()
        return (yield Resume(k, f"{delegated}!"))

    wrapped = _with_handlers(greet_world(), interceptor, greet_handler)
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    assert result.value == "hello world!"


def test_inner_handler_resumes_then_raises_in_nested_dispatch() -> None:
    def inner(effect: object, k: object):
        if not isinstance(effect, Greet):
            yield Pass()
            return
        delegated = yield Delegate()
        _ = yield Resume(k, delegated)
        raise RuntimeError("inner post-resume boom")

    def outer(effect: object, k: object):
        if not isinstance(effect, Greet):
            yield Pass()
            return
        _ = yield Noop(tag="outer-pre")
        return (yield Resume(k, f"hello {effect.name}"))

    wrapped = _with_handlers(greet_world(), inner, outer, noop_handler)
    result = run(wrapped, handlers=default_handlers())
    assert _is_err(result)
    assert isinstance(result.error, RuntimeError)
    assert "inner post-resume boom" in str(result.error)
