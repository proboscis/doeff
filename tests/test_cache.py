"""Tests for cache effects and decorator."""

import pickle
import time

import pytest

from doeff import (
    CacheGet,
    CacheLifecycle,
    CachePut,
    CacheStorage,
    EffectGenerator,
    ExecutionContext,
    ProgramInterpreter,
    Recover,
    do,
)
from doeff._vendor import FrozenDict
from doeff.cache import cache, cache_1min, cache_key

# Test cache effects directly

@pytest.mark.asyncio
async def test_cache_put_and_get():
    """Test basic cache put and get operations."""

    @do
    def test_program():
        # Put a value in cache
        yield CachePut("test_key", "test_value")
        # Get it back
        value = yield CacheGet("test_key")
        return value

    engine = ProgramInterpreter()
    result = await engine.run(test_program())

    assert result.is_ok
    assert result.value == "test_value"


@pytest.mark.asyncio
async def test_cache_miss_raises_error():
    """Test that cache miss raises KeyError."""

    @do
    def test_program():
        # Try to get non-existent key
        value = yield CacheGet("nonexistent")
        return value

    engine = ProgramInterpreter()
    result = await engine.run(test_program())

    assert result.is_err
    assert "Cache miss" in str(result.result.error)


@pytest.mark.asyncio
async def test_cache_with_complex_key():
    """Test cache with tuple and FrozenDict key."""

    @do
    def test_program():
        # Use complex key
        key = ("func_name", (1, 2, 3), FrozenDict({"a": 1, "b": 2}))
        yield CachePut(key, "complex_value")
        value = yield CacheGet(key)
        return value

    engine = ProgramInterpreter()
    result = await engine.run(test_program())

    assert result.is_ok
    assert result.value == "complex_value"


@pytest.mark.asyncio
async def test_cache_ttl_expiry():
    """Test that cached values expire after TTL."""

    # We need to wrap the sleep in an IO effect
    @do
    def test_with_io():
        from doeff import IO
        # Put with very short TTL
        yield CachePut("ttl_key", "ttl_value", ttl=0.1)  # 100ms

        # Get immediately - should work
        value1 = yield CacheGet("ttl_key")

        # Sleep for longer than TTL
        yield IO(lambda: time.sleep(0.2))

        # Use Recover to handle the expected cache miss
        @do
        def on_cache_miss():
            return "expired"

        value2 = yield Recover(CacheGet("ttl_key"), on_cache_miss())

        return (value1, value2)

    engine = ProgramInterpreter()
    result = await engine.run(test_with_io())

    assert result.is_ok
    assert result.value[0] == "ttl_value"
    assert result.value[1] == "expired"


@pytest.mark.asyncio
async def test_cache_persistent_lifecycle_uses_disk():
    """Cache entries with persistent lifecycle should be written to disk."""

    engine = ProgramInterpreter()
    cache_dir = engine.cache_handler._disk_cache_dir
    existing = set(cache_dir.iterdir()) if cache_dir.exists() else set()

    key = ("disk", 1)

    @do
    def program():
        yield CachePut(key, {"value": 42}, lifecycle=CacheLifecycle.PERSISTENT)
        return (yield CacheGet(key))

    result = await engine.run(program())

    assert result.is_ok
    assert result.value == {"value": 42}

    after = set(cache_dir.iterdir()) if cache_dir.exists() else set()
    new_files = after - existing
    try:
        assert len(new_files) == 1
        new_file = next(iter(new_files))
        with new_file.open("rb") as fh:
            stored = pickle.load(fh)
        assert stored == {"value": 42}
    finally:
        for path in new_files:
            if path.exists():
                path.unlink()


@pytest.mark.asyncio
async def test_cache_explicit_storage_disk():
    """Explicit disk storage hint should persist values on disk."""

    engine = ProgramInterpreter()
    cache_dir = engine.cache_handler._disk_cache_dir
    existing = set(cache_dir.iterdir()) if cache_dir.exists() else set()

    @do
    def program():
        yield CachePut("disk_key", "value", storage=CacheStorage.DISK)
        return (yield CacheGet("disk_key"))

    result = await engine.run(program())

    assert result.is_ok
    assert result.value == "value"

    after = set(cache_dir.iterdir()) if cache_dir.exists() else set()
    new_files = after - existing
    try:
        assert len(new_files) == 1
        file_path = next(iter(new_files))
        with file_path.open("rb") as fh:
            stored_value = pickle.load(fh)
        assert stored_value == "value"
    finally:
        for path in new_files:
            if path.exists():
                path.unlink()


@pytest.mark.asyncio
async def test_cache_recover_on_miss():
    """Test using Recover effect with cache miss."""

    @do
    def test_program():
        # Define fallback computation
        @do
        def compute_value():
            return "computed_value"

        # Try cache get with recovery
        value = yield Recover(CacheGet("missing_key"), compute_value())
        return value

    engine = ProgramInterpreter()
    result = await engine.run(test_program())

    assert result.is_ok
    assert result.value == "computed_value"


# Test the @cache decorator

@pytest.mark.asyncio
async def test_basic_cache_decorator():
    """Test basic caching with decorator."""

    call_count = [0]

    @cache()
    @do
    def expensive_computation(x: int) -> EffectGenerator[int]:
        call_count[0] += 1
        return x * 2

    @do
    def test_program():
        # First call - should compute
        result1 = yield expensive_computation(5)
        # Second call - should use cache
        result2 = yield expensive_computation(5)
        # Different args - should compute
        result3 = yield expensive_computation(10)

        return (result1, result2, result3, call_count[0])

    engine = ProgramInterpreter()
    result = await engine.run(test_program())

    assert result.is_ok
    assert result.value[0] == 10  # 5 * 2
    assert result.value[1] == 10  # Cached
    assert result.value[2] == 20  # 10 * 2
    assert result.value[3] == 2    # Called twice (once for 5, once for 10)


@pytest.mark.asyncio
async def test_cache_with_kwargs():
    """Test caching with keyword arguments."""

    call_count = [0]

    @cache()
    @do
    def process_data(x: int, multiply: bool = False) -> EffectGenerator[int]:
        call_count[0] += 1
        if multiply:
            return x * 3
        return x + 1

    @do
    def test_program():
        # Different kwargs should have different cache keys
        result1 = yield process_data(5)
        result2 = yield process_data(5, multiply=False)  # Same as result1
        result3 = yield process_data(5, multiply=True)   # Different
        result4 = yield process_data(5)  # Cached from result1

        return (result1, result2, result3, result4, call_count[0])

    engine = ProgramInterpreter()
    result = await engine.run(test_program())

    assert result.is_ok
    assert result.value[0] == 6   # 5 + 1
    assert result.value[1] == 6   # Cached
    assert result.value[2] == 15  # 5 * 3
    assert result.value[3] == 6   # Cached
    assert result.value[4] == 2   # Called twice


@pytest.mark.asyncio
async def test_cache_with_ttl():
    """Test cache decorator with TTL."""

    call_count = [0]

    @cache(ttl=0.2)  # 200ms TTL
    @do
    def get_timestamp() -> EffectGenerator[float]:
        from doeff import IO
        call_count[0] += 1
        # Use IO effect for time.time()
        timestamp = yield IO(time.time)
        return timestamp

    @do
    def test_program():
        from doeff import IO

        # First call
        time1 = yield get_timestamp()

        # Immediate second call - should be cached
        time2 = yield get_timestamp()

        # Wait for TTL to expire
        yield IO(lambda: time.sleep(0.3))

        # Third call - cache expired, should recompute
        time3 = yield get_timestamp()

        return (time1, time2, time3, call_count[0])

    engine = ProgramInterpreter()
    result = await engine.run(test_program())

    assert result.is_ok
    assert result.value[0] == result.value[1]  # Same cached value
    assert result.value[2] > result.value[1]   # New value after expiry
    assert result.value[3] == 2  # Called twice


@pytest.mark.asyncio
async def test_cache_key_selector():
    """Test custom cache key selection."""

    call_count = [0]

    @cache(key_func=cache_key("user_id"))
    @do
    def get_user_data(user_id: int, include_details: bool = False) -> EffectGenerator[str]:
        call_count[0] += 1
        if include_details:
            return f"User {user_id} with details"
        return f"User {user_id}"

    @do
    def test_program():
        # These should all use the same cache key (only user_id matters)
        result1 = yield get_user_data(1, include_details=False)
        result2 = yield get_user_data(1, include_details=True)  # Same cache
        result3 = yield get_user_data(2, include_details=False)  # Different cache

        return (result1, result2, result3, call_count[0])

    engine = ProgramInterpreter()
    result = await engine.run(test_program())

    assert result.is_ok
    assert result.value[0] == "User 1"
    assert result.value[1] == "User 1"  # Cached, ignores include_details
    assert result.value[2] == "User 2"
    assert result.value[3] == 2  # Called twice (once for user 1, once for user 2)


@pytest.mark.asyncio
async def test_convenience_decorators():
    """Test convenience cache decorators."""

    @cache_1min
    @do
    def cached_1min() -> EffectGenerator[str]:
        return "1min"

    @do
    def test_program():
        result = yield cached_1min()
        return result

    engine = ProgramInterpreter()
    result = await engine.run(test_program())

    assert result.is_ok
    assert result.value == "1min"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])



@pytest.mark.asyncio
async def test_cache_decorator_hits():
    calls = []

    @cache()
    @do
    def compute(x: int) -> EffectGenerator[int]:
        calls.append(x)
        return x * 2

    interpreter = ProgramInterpreter()
    context = ExecutionContext()

    result1 = await interpreter.run(compute(3), context)
    assert result1.value == 6
    assert calls == [3]

    result2 = await interpreter.run(compute(3), result1.context)
    assert result2.value == 6
    assert calls == [3]


@pytest.mark.asyncio
async def test_cache_decorator_expiry():
    calls = []

    @cache(ttl=0.5)
    @do
    def compute(x: int) -> EffectGenerator[int]:
        calls.append(x)
        return x * 2

    interpreter = ProgramInterpreter()
    context = ExecutionContext()
    cache_handler = interpreter.cache_handler
    base_time = 1000.0
    original_time = cache_handler._time

    try:
        cache_handler._time = lambda: base_time
        await interpreter.run(compute(5), context)
        assert calls == [5]

        cache_handler._time = lambda: base_time + 0.2
        await interpreter.run(compute(5), context)
        assert calls == [5]

        cache_handler._time = lambda: base_time + 1.0
        await interpreter.run(compute(5), context)
        assert calls == [5, 5]
    finally:
        cache_handler._time = original_time
