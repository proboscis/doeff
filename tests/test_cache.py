"""Tests for cache effects and decorator."""

import asyncio
import os
import sys
import textwrap
import time
from typing import Any, Mapping

import pytest

from doeff import (
    CacheGet,
    CacheLifecycle,
    CachePut,
    CacheStorage,
    MemoGet,
    MemoPut,
    EffectGenerator,
    ExecutionContext,
    ProgramInterpreter,
    Recover,
    do,
)
from doeff._vendor import FrozenDict
from doeff.cache import cache, cache_1min, cache_key
from doeff.handlers import HandlerScope

# Shared fixture ensuring each test uses isolated cache database


@pytest.fixture
def temp_cache_db(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.sqlite3"
    monkeypatch.setenv("DOEFF_CACHE_PATH", str(db_path))
    yield db_path
    monkeypatch.delenv("DOEFF_CACHE_PATH", raising=False)

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
async def test_cache_ttl_expiry(temp_cache_db):
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
async def test_cache_persistent_lifecycle_uses_disk(temp_cache_db):
    """Cache entries with persistent lifecycle persist across interpreter instances."""

    engine = ProgramInterpreter()

    key = ("disk", 1)

    @do
    def store():
        yield CachePut(key, {"value": 42}, lifecycle=CacheLifecycle.PERSISTENT)

    @do
    def fetch():
        return (yield CacheGet(key))

    await engine.run(store())
    assert temp_cache_db.exists()

    second_engine = ProgramInterpreter()
    result = await second_engine.run(fetch())

    assert result.is_ok
    assert result.value == {"value": 42}


@pytest.mark.asyncio
async def test_cache_explicit_storage_disk(temp_cache_db):
    """Explicit disk storage hint should persist values on disk."""

    engine = ProgramInterpreter()

    @do
    def store_and_fetch():
        yield CachePut("disk_key", "value", storage=CacheStorage.DISK)
        return (yield CacheGet("disk_key"))

    result = await engine.run(store_and_fetch())

    assert result.is_ok
    assert result.value == "value"
    assert temp_cache_db.exists()


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
async def test_basic_cache_decorator(temp_cache_db):
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
async def test_cache_decorator_persistent_lifecycle(temp_cache_db):
    """Decorator should propagate persistent lifecycle and reuse cached value."""

    call_count = [0]

    class RecordingCacheHandler:
        scope = HandlerScope.SHARED

        def __init__(self):
            self.store: dict[Any, Any] = {}
            self.policies: list[Any] = []

        async def handle_get(self, effect, ctx):
            if effect.key not in self.store:
                raise KeyError("miss")
            return self.store[effect.key]

        async def handle_put(self, effect, ctx):
            self.store[effect.key] = effect.value
            self.policies.append(effect.policy)

    cache_handler = RecordingCacheHandler()
    engine = ProgramInterpreter(custom_handlers={"cache": cache_handler})

    @cache(lifecycle=CacheLifecycle.PERSISTENT)
    @do
    def expensive(x: int) -> EffectGenerator[int]:
        call_count[0] += 1
        return x * 3

    first = await engine.run(expensive(4))
    assert first.is_ok
    assert first.value == 12
    assert call_count[0] == 1
    assert cache_handler.policies
    assert cache_handler.policies[-1].lifecycle is CacheLifecycle.PERSISTENT

    second = await engine.run(expensive(4))
    assert second.is_ok
    assert second.value == 12
    assert call_count[0] == 1  # cache hit


@pytest.mark.asyncio
async def test_cache_decorator_persistent_lifecycle_persists(temp_cache_db):
    """Persistent lifecycle hint should keep data across interpreter instances."""

    call_count = [0]

    @cache(lifecycle=CacheLifecycle.PERSISTENT)
    @do
    def expensive_value() -> EffectGenerator[str]:
        call_count[0] += 1
        return "value"

    engine_one = ProgramInterpreter()
    first = await engine_one.run(expensive_value())

    assert first.is_ok
    assert first.value == "value"
    assert call_count[0] == 1
    assert temp_cache_db.exists()

    engine_two = ProgramInterpreter()
    second = await engine_two.run(expensive_value())

    assert second.is_ok
    assert second.value == "value"
    # Should still be 1 because cache hit happens in new interpreter
    assert call_count[0] == 1


@pytest.mark.asyncio
async def test_cache_persistent_lifecycle_cross_process(temp_cache_db):
    """Persistent lifecycle cache survives across different Python processes."""

    script = textwrap.dedent(
        """
        import asyncio
        import sys
        from doeff import CacheGet, CachePut, CacheLifecycle, ProgramInterpreter, do

        CACHE_KEY = ("cross_process", 42)

        @do
        def store():
            yield CachePut(CACHE_KEY, "persisted", lifecycle=CacheLifecycle.PERSISTENT)

        @do
        def load():
            return (yield CacheGet(CACHE_KEY))

        async def run(mode: str) -> None:
            engine = ProgramInterpreter()
            if mode == "store":
                result = await engine.run(store())
                if result.is_err:
                    raise SystemExit(f"store failed: {result.result.error!r}")
            elif mode == "load":
                result = await engine.run(load())
                if result.is_err:
                    raise result.result.error
                if result.value != "persisted":
                    raise SystemExit(f"unexpected value: {result.value!r}")
            else:
                raise SystemExit(f"unknown mode: {mode}")

        if __name__ == "__main__":
            asyncio.run(run(sys.argv[1]))
        """
    )

    env = os.environ.copy()
    env["DOEFF_CACHE_PATH"] = str(temp_cache_db)

    async def run_mode(mode: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            mode,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise AssertionError(
                f"Process {mode} failed with code {proc.returncode}\n"
                f"STDOUT: {stdout.decode()}\nSTDERR: {stderr.decode()}"
            )
        return stdout.decode()

    await run_mode("store")
    assert temp_cache_db.exists()
    await run_mode("load")


@pytest.mark.asyncio
async def test_cache_with_kwargs(temp_cache_db):
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
async def test_cache_with_ttl(temp_cache_db):
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
async def test_cache_key_selector(temp_cache_db):
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
async def test_cache_key_hashers_transform_arguments(temp_cache_db):
    """Test key_hashers applies transformations for positional and keyword args."""

    calls = []

    def dict_hasher(data: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
        return tuple(sorted(data.items()))

    hasher_runs: list[str] = []

    @do
    def extract_id(metadata: Mapping[str, Any]) -> EffectGenerator[Any]:
        hasher_runs.append(metadata["id"])
        return metadata.get("id")

    @cache(key_hashers={"payload": dict_hasher, "extra": extract_id})
    @do
    def compute(user_id: int, payload: dict[str, int], *, extra: dict[str, str]) -> EffectGenerator[int]:
        calls.append((user_id, payload["value"]))
        return payload["value"]

    interpreter = ProgramInterpreter()
    context = ExecutionContext()

    payload_one = {"value": 1, "other": 99}
    payload_two = {"other": 99, "value": 1}
    extra_one = {"id": "alpha", "note": "first"}
    extra_two = {"note": "first", "id": "alpha"}

    result1 = await interpreter.run(compute(7, payload_one, extra=extra_one), context)
    assert result1.value == 1

    result2 = await interpreter.run(
        compute(7, payload_two, extra=extra_two),
        result1.context,
    )
    assert result2.value == 1

    assert calls == [(7, 1)]
    assert hasher_runs == ["alpha", "alpha"]


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


@pytest.mark.asyncio
async def test_cache_decorator_hits(temp_cache_db):
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
async def test_cache_decorator_expiry(temp_cache_db):
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


@pytest.mark.asyncio
async def test_memo_effects():
    @do
    def program() -> EffectGenerator[int]:
        yield MemoPut("alpha", 123)
        return (yield MemoGet("alpha"))

    interpreter = ProgramInterpreter()
    result = await interpreter.run(program())

    assert result.is_ok
    assert result.value == 123


@pytest.mark.asyncio
async def test_memo_miss_raises_keyerror():
    @do
    def program() -> EffectGenerator[int]:
        return (yield MemoGet("missing"))

    interpreter = ProgramInterpreter()
    result = await interpreter.run(program())

    assert result.is_err
    assert "Memo miss" in str(result.result.error)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


@pytest.mark.asyncio
async def test_cache_decorator_accepts_pil(temp_cache_db):
    from PIL import Image

    calls = []

    @cache()
    @do
    def compute(image: Image.Image) -> EffectGenerator[int]:
        calls.append(image.size)
        return image.width * image.height

    image = Image.new("RGB", (16, 16), color="red")

    interpreter = ProgramInterpreter()
    context = ExecutionContext()

    result1 = await interpreter.run(compute(image), context)
    assert result1.value == 256
    assert calls == [(16, 16)]

    result2 = await interpreter.run(compute(image), result1.context)
    assert result2.value == 256
    assert calls == [(16, 16)]
