from __future__ import annotations

from dataclasses import dataclass

from doeff import (
    CacheGet,
    CachePut,
    Delegate,
    EffectGenerator,
    Pass,
    Resume,
    default_handlers,
    do,
    run,
)
from doeff.effects.base import EffectBase
from doeff.effects.cache import CacheGetEffect, CachePutEffect


@dataclass(frozen=True, kw_only=True)
class Greet(EffectBase):
    name: str


@dataclass(frozen=True, kw_only=True)
class Noop(EffectBase):
    tag: str = "noop"


def greet_handler(effect, k):
    if not isinstance(effect, Greet):
        yield Pass()
        return
    return (yield Resume(k, f"hello {effect.name}"))


def noop_handler(effect, k):
    if not isinstance(effect, Noop):
        yield Pass()
        return
    return (yield Resume(k, effect.tag))


@do
def main() -> EffectGenerator[str]:
    return (yield Greet(name="world"))


def test_effect_yield_then_delegate_basic() -> None:
    """Test 1: Handler yields effect then Delegate()."""

    def interceptor(effect, k):
        if not isinstance(effect, Greet):
            yield Pass()
            return
        _ = yield Noop()  # any effect yield here must not poison dispatch stack
        result = yield Delegate()
        return (yield Resume(k, result))

    result = run(main(), handlers=[*default_handlers(), noop_handler, greet_handler, interceptor])
    assert result.is_ok(), f"Expected OK, got: {result.error}"
    assert result.value == "hello world"


def test_effect_yield_then_delegate_multiple_effects() -> None:
    """Test 2: Multiple effect yields before Delegate()."""

    def interceptor(effect, k):
        if not isinstance(effect, Greet):
            yield Pass()
            return
        first = yield Noop(tag="first")
        second = yield Noop(tag="second")
        result = yield Delegate()
        return (yield Resume(k, f"{result}:{first}:{second}"))

    result = run(main(), handlers=[*default_handlers(), noop_handler, greet_handler, interceptor])
    assert result.is_ok(), f"Expected OK, got: {result.error}"
    assert result.value == "hello world:first:second"


def test_cache_miss_then_delegate_then_cache_put() -> None:
    """Test 3: Cache pattern — CacheGet miss, Delegate, then CachePut."""

    cache_store: dict[object, object] = {}
    delegated_calls = {"count": 0}

    def cache_handler(effect, k):
        if isinstance(effect, CacheGetEffect):
            return (yield Resume(k, cache_store.get(effect.key)))
        if isinstance(effect, CachePutEffect):
            cache_store[effect.key] = effect.value
            return (yield Resume(k, None))
        yield Pass()

    def counting_greet_handler(effect, k):
        if not isinstance(effect, Greet):
            yield Pass()
            return
        delegated_calls["count"] += 1
        return (yield Resume(k, f"hello {effect.name}"))

    def caching_interceptor(effect, k):
        if not isinstance(effect, Greet):
            yield Pass()
            return
        cache_key = ("greet", effect.name)
        cached = yield CacheGet(cache_key)
        if cached is not None:
            return (yield Resume(k, cached))
        delegated = yield Delegate()
        _ = yield CachePut(cache_key, delegated)
        return (yield Resume(k, delegated))

    handlers = [cache_handler, counting_greet_handler, caching_interceptor]

    first = run(main(), handlers=handlers)
    assert first.is_ok(), f"Expected OK, got: {first.error}"
    assert first.value == "hello world"
    assert delegated_calls["count"] == 1

    second = run(main(), handlers=handlers)
    assert second.is_ok(), f"Expected OK, got: {second.error}"
    assert second.value == "hello world"
    assert delegated_calls["count"] == 1


def test_nested_handler_effects_preserve_outer_dispatch_context() -> None:
    """Test 4: Nested handler effects must not corrupt outer dispatch context."""

    call_order: list[str] = []

    def inner_interceptor(effect, k):
        if not isinstance(effect, Greet):
            yield Pass()
            return
        call_order.append("inner:before")
        _ = yield Noop(tag="inner")
        delegated = yield Delegate()
        call_order.append("inner:after")
        return (yield Resume(k, f"{delegated}|inner"))

    def outer_interceptor(effect, k):
        if not isinstance(effect, Greet):
            yield Pass()
            return
        call_order.append("outer:before")
        _ = yield Noop(tag="outer")
        delegated = yield Delegate()
        call_order.append("outer:after")
        return (yield Resume(k, f"{delegated}|outer"))

    result = run(
        main(),
        handlers=[*default_handlers(), noop_handler, greet_handler, inner_interceptor, outer_interceptor],
    )
    assert result.is_ok(), f"Expected OK, got: {result.error}"
    assert result.value == "hello world|inner|outer"
    assert call_order == ["outer:before", "inner:before", "inner:after", "outer:after"]


def test_delegate_without_prior_effect_yield_regression_guard() -> None:
    """Test 5: Delegate path still works when no effect is yielded before delegating."""

    def interceptor(effect, k):
        if not isinstance(effect, Greet):
            yield Pass()
            return
        delegated = yield Delegate()
        return (yield Resume(k, f"{delegated}!"))

    result = run(main(), handlers=[*default_handlers(), greet_handler, interceptor])
    assert result.is_ok(), f"Expected OK, got: {result.error}"
    assert result.value == "hello world!"
