from __future__ import annotations

from doeff.cesk_v3.level2_algebraic_effects.primitives import WithHandler
from doeff.cesk_v3.level3_core_effects import (
    CACHE_MISS,
    CacheDelete,
    CacheDeleteEffect,
    CacheExists,
    CacheExistsEffect,
    CacheGet,
    CacheGetEffect,
    CachePut,
    CachePutEffect,
    cache_handler,
    state_handler,
    Get,
    Put,
)
from doeff.cesk_v3.run import sync_run
from doeff.do import do
from doeff.program import Program


class TestCacheEffectTypes:
    def test_cache_get_creates_effect(self):
        effect = CacheGet("key")
        assert isinstance(effect, CacheGetEffect)
        assert effect.key == "key"

    def test_cache_put_creates_effect(self):
        effect = CachePut("key", "value")
        assert isinstance(effect, CachePutEffect)
        assert effect.key == "key"
        assert effect.value == "value"

    def test_cache_delete_creates_effect(self):
        effect = CacheDelete("key")
        assert isinstance(effect, CacheDeleteEffect)
        assert effect.key == "key"

    def test_cache_exists_creates_effect(self):
        effect = CacheExists("key")
        assert isinstance(effect, CacheExistsEffect)
        assert effect.key == "key"


class TestCacheMissSentinel:
    def test_cache_miss_is_falsy(self):
        assert not CACHE_MISS
        assert bool(CACHE_MISS) is False

    def test_cache_miss_repr(self):
        assert repr(CACHE_MISS) == "CACHE_MISS"

    def test_cache_miss_identity(self):
        result = CACHE_MISS
        assert result is CACHE_MISS


class TestCacheHandler:
    def test_cache_put_and_get(self):
        @do
        def program() -> Program[str]:
            yield CachePut("key", "value")
            return (yield CacheGet("key"))

        handler, _ = cache_handler()
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() == "value"

    def test_cache_get_missing_returns_cache_miss(self):
        @do
        def program() -> Program[bool]:
            result = yield CacheGet("nonexistent")
            return result is CACHE_MISS

        handler, _ = cache_handler()
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() is True

    def test_cache_exists_returns_true_for_existing(self):
        @do
        def program() -> Program[bool]:
            yield CachePut("key", "value")
            return (yield CacheExists("key"))

        handler, _ = cache_handler()
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() is True

    def test_cache_exists_returns_false_for_missing(self):
        @do
        def program() -> Program[bool]:
            return (yield CacheExists("nonexistent"))

        handler, _ = cache_handler()
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() is False

    def test_cache_delete_existing_returns_true(self):
        @do
        def program() -> Program[bool]:
            yield CachePut("key", "value")
            return (yield CacheDelete("key"))

        handler, _ = cache_handler()
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() is True

    def test_cache_delete_missing_returns_false(self):
        @do
        def program() -> Program[bool]:
            return (yield CacheDelete("nonexistent"))

        handler, _ = cache_handler()
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() is False

    def test_cache_delete_removes_value(self):
        @do
        def program() -> Program[bool]:
            yield CachePut("key", "value")
            yield CacheDelete("key")
            result = yield CacheGet("key")
            return result is CACHE_MISS

        handler, _ = cache_handler()
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() is True

    def test_cache_overwrite(self):
        @do
        def program() -> Program[str]:
            yield CachePut("key", "first")
            yield CachePut("key", "second")
            return (yield CacheGet("key"))

        handler, _ = cache_handler()
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() == "second"

    def test_initial_cache(self):
        @do
        def program() -> Program[str]:
            return (yield CacheGet("preloaded"))

        handler, _ = cache_handler(initial_cache={"preloaded": "initial_value"})
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() == "initial_value"

    def test_handler_returns_cache_dict(self):
        @do
        def program() -> Program[None]:
            yield CachePut("a", 1)
            yield CachePut("b", 2)
            return None

        handler, cache = cache_handler()
        sync_run(WithHandler(handler, program()))
        assert cache == {"a": 1, "b": 2}


class TestCacheWithOtherEffects:
    def test_cache_with_state(self):
        @do
        def program() -> Program[int]:
            cached = yield CacheGet("computation")
            if cached is CACHE_MISS:
                yield Put("cache_misses", 1)
                yield CachePut("computation", 42)
                return 42
            yield Put("cache_misses", 0)
            return cached

        cache_h, _ = cache_handler()
        result = sync_run(
            WithHandler(state_handler(), WithHandler(cache_h, program()))
        )
        assert result.unwrap() == 42


class TestCacheEdgeCases:
    def test_cache_none_value(self):
        @do
        def program() -> Program[tuple]:
            yield CachePut("key", None)
            result = yield CacheGet("key")
            exists = yield CacheExists("key")
            return (result, exists)

        handler, _ = cache_handler()
        result = sync_run(WithHandler(handler, program()))
        value, exists = result.unwrap()
        assert value is None
        assert exists is True

    def test_cache_complex_key(self):
        @do
        def program() -> Program[str]:
            key = ("module", "function", (1, 2, 3))
            yield CachePut(key, "cached_result")
            return (yield CacheGet(key))

        handler, _ = cache_handler()
        result = sync_run(WithHandler(handler, program()))
        assert result.unwrap() == "cached_result"

    def test_cache_miss_pattern(self):
        call_count = [0]

        @do
        def expensive_computation() -> Program[int]:
            call_count[0] += 1
            return 42

        @do
        def cached_computation() -> Program[int]:
            result = yield CacheGet("expensive")
            if result is CACHE_MISS:
                value = yield expensive_computation()
                yield CachePut("expensive", value)
                return value
            return result

        @do
        def program() -> Program[tuple]:
            first = yield cached_computation()
            second = yield cached_computation()
            return (first, second)

        handler, _ = cache_handler()
        result = sync_run(WithHandler(handler, program()))
        first, second = result.unwrap()
        assert first == 42
        assert second == 42
        assert call_count[0] == 1

    def test_multiple_independent_caches(self):
        @do
        def program_a() -> Program[str]:
            yield CachePut("key", "a_value")
            return (yield CacheGet("key"))

        @do
        def program_b() -> Program[str]:
            result = yield CacheGet("key")
            if result is CACHE_MISS:
                return "not_found"
            return result

        handler_a, cache_a = cache_handler()
        handler_b, cache_b = cache_handler()

        result_a = sync_run(WithHandler(handler_a, program_a()))
        result_b = sync_run(WithHandler(handler_b, program_b()))

        assert result_a.unwrap() == "a_value"
        assert result_b.unwrap() == "not_found"
        assert cache_a == {"key": "a_value"}
        assert cache_b == {}
