"""Tests for cache effects and decorator."""

import asyncio
import sqlite3
import sys
import textwrap
import time
from collections.abc import Mapping
from typing import Any

import pytest

from doeff import (
    CacheGet,
    CacheLifecycle,
    CachePut,
    CacheStorage,
    EffectGenerator,
    ExecutionContext,
    MemoGet,
    MemoPut,
    ProgramInterpreter,
    Recover,
    do,
)
from doeff._vendor import FrozenDict
from doeff.cache import (
    CACHE_PATH_ENV_KEY,
    CacheComputationError,
    cache,
    cache_1min,
    cache_key,
    clear_persistent_cache,
    persistent_cache_path,
)
from doeff.types import EffectFailureError
from doeff.handlers import HandlerScope

# Shared fixture ensuring each test uses isolated cache database


@pytest.fixture
def temp_cache_db(tmp_path):
    """Return a temporary cache database path for test isolation."""
    return tmp_path / "cache.sqlite3"


@pytest.fixture
def cache_context(temp_cache_db):
    """Return an ExecutionContext configured with the temp cache path."""
    return ExecutionContext(env={CACHE_PATH_ENV_KEY: temp_cache_db})


def test_persistent_cache_path_returns_default() -> None:
    """persistent_cache_path returns the default temp directory path."""
    import tempfile
    from pathlib import Path

    expected = Path(tempfile.gettempdir()) / "doeff_cache.sqlite3"
    assert persistent_cache_path() == expected


@pytest.mark.asyncio
async def test_clear_persistent_cache(temp_cache_db, cache_context) -> None:
    @do
    def cache_roundtrip():
        yield CachePut("clear-key", "value", lifecycle=CacheLifecycle.PERSISTENT)
        return (yield CacheGet("clear-key"))

    engine = ProgramInterpreter()
    result = await engine.run_async(cache_roundtrip(), cache_context)

    assert result.is_ok
    assert result.value == "value"

    with sqlite3.connect(temp_cache_db) as conn:
        count_before = conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]

    assert count_before == 1

    cleared_path = clear_persistent_cache(temp_cache_db)

    assert cleared_path == temp_cache_db

    with sqlite3.connect(temp_cache_db) as conn:
        count_after = conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]

    assert count_after == 0

# Test cache effects directly

@pytest.mark.asyncio
async def test_cache_put_and_get() -> None:
    """Test basic cache put and get operations."""

    @do
    def test_program():
        # Put a value in cache
        yield CachePut("test_key", "test_value")
        # Get it back
        value = yield CacheGet("test_key")
        return value

    engine = ProgramInterpreter()
    result = await engine.run_async(test_program())

    assert result.is_ok
    assert result.value == "test_value"


@pytest.mark.asyncio
async def test_cache_miss_raises_error() -> None:
    """Test that cache miss raises KeyError."""

    @do
    def test_program():
        # Try to get non-existent key
        value = yield CacheGet("nonexistent")
        return value

    engine = ProgramInterpreter()
    result = await engine.run_async(test_program())

    assert result.is_err
    assert "Cache miss" in str(result.result.error)


@pytest.mark.asyncio
async def test_cache_with_complex_key() -> None:
    """Test cache with tuple and FrozenDict key."""

    @do
    def test_program():
        # Use complex key
        key = ("func_name", (1, 2, 3), FrozenDict({"a": 1, "b": 2}))
        yield CachePut(key, "complex_value")
        value = yield CacheGet(key)
        return value

    engine = ProgramInterpreter()
    result = await engine.run_async(test_program())

    assert result.is_ok
    assert result.value == "complex_value"


@pytest.mark.asyncio
async def test_cache_ttl_expiry(temp_cache_db) -> None:
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
    result = await engine.run_async(test_with_io())

    assert result.is_ok
    assert result.value[0] == "ttl_value"
    assert result.value[1] == "expired"


@pytest.mark.asyncio
async def test_cache_persistent_lifecycle_uses_disk(temp_cache_db, cache_context) -> None:
    """Cache entries with persistent lifecycle persist across interpreter instances."""

    engine = ProgramInterpreter()

    key = ("disk", 1)

    @do
    def store():
        yield CachePut(key, {"value": 42}, lifecycle=CacheLifecycle.PERSISTENT)

    @do
    def fetch():
        return (yield CacheGet(key))

    await engine.run_async(store(), cache_context)
    assert temp_cache_db.exists()

    # Second engine with same cache path via context
    second_context = ExecutionContext(env={CACHE_PATH_ENV_KEY: temp_cache_db})
    second_engine = ProgramInterpreter()
    result = await second_engine.run_async(fetch(), second_context)

    assert result.is_ok
    assert result.value == {"value": 42}


@pytest.mark.asyncio
async def test_cache_explicit_storage_disk(temp_cache_db, cache_context) -> None:
    """Explicit disk storage hint should persist values on disk."""

    engine = ProgramInterpreter()

    @do
    def store_and_fetch():
        yield CachePut("disk_key", "value", storage=CacheStorage.DISK)
        return (yield CacheGet("disk_key"))

    result = await engine.run_async(store_and_fetch(), cache_context)

    assert result.is_ok
    assert result.value == "value"
    assert temp_cache_db.exists()


@pytest.mark.asyncio
async def test_cache_recover_on_miss() -> None:
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
    result = await engine.run_async(test_program())

    assert result.is_ok
    assert result.value == "computed_value"


# Test the @cache decorator

@pytest.mark.asyncio
async def test_basic_cache_decorator(temp_cache_db, cache_context) -> None:
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
    result = await engine.run_async(test_program(), cache_context)

    assert result.is_ok
    assert result.value[0] == 10  # 5 * 2
    assert result.value[1] == 10  # Cached
    assert result.value[2] == 20  # 10 * 2
    assert result.value[3] == 2    # Called twice (once for 5, once for 10)


@pytest.mark.asyncio
async def test_cache_decorator_failure_includes_call_frame() -> None:
    """Failures should raise CacheComputationError with call-site frame present."""

    @cache()
    @do
    def failing_func(x: int) -> EffectGenerator[int]:
        raise ValueError("boom")

    engine = ProgramInterpreter()

    result = await engine.run_async(failing_func(5))

    assert result.is_err

    failure = result.result.error
    assert isinstance(failure, EffectFailureError)

    # Navigate through nested EffectFailureError to find CacheComputationError
    # The cache decorator raises CacheComputationError which may be wrapped in EffectFailure
    cause = failure.cause
    while isinstance(cause, EffectFailureError):
        cause = cause.cause
    assert isinstance(cause, CacheComputationError)
    # Inner cause is also wrapped in EffectFailure (from the @do function raising)
    inner_cause = cause.__cause__
    while isinstance(inner_cause, EffectFailureError):
        inner_cause = inner_cause.cause
    assert isinstance(inner_cause, ValueError)
    assert cause.call_site is not None
    assert (
        cause.call_site.function == "test_cache_decorator_failure_includes_call_frame"
    )
    assert cause.call_site.filename.endswith("test_cache.py")


@pytest.mark.asyncio
async def test_cache_decorator_persistent_lifecycle(temp_cache_db) -> None:
    """Decorator should propagate persistent lifecycle and reuse cached value."""

    call_count = [0]

    class RecordingCacheHandler:
        scope = HandlerScope.SHARED

        def __init__(self):
            self.store: dict[Any, Any] = {}
            self.policies: list[Any] = []

        async def handle_get(self, effect, _ctx):
            if effect.key not in self.store:
                raise KeyError("miss")
            return self.store[effect.key]

        async def handle_put(self, effect, _ctx):
            self.store[effect.key] = effect.value
            self.policies.append(effect.policy)

    cache_handler = RecordingCacheHandler()
    engine = ProgramInterpreter(custom_handlers={"cache": cache_handler})

    @cache(lifecycle=CacheLifecycle.PERSISTENT)
    @do
    def expensive(x: int) -> EffectGenerator[int]:
        call_count[0] += 1
        return x * 3

    first = await engine.run_async(expensive(4))
    assert first.is_ok
    assert first.value == 12
    assert call_count[0] == 1
    assert cache_handler.policies
    assert cache_handler.policies[-1].lifecycle is CacheLifecycle.PERSISTENT

    second = await engine.run_async(expensive(4))
    assert second.is_ok
    assert second.value == 12
    assert call_count[0] == 1  # cache hit


@pytest.mark.asyncio
async def test_cache_decorator_persistent_lifecycle_persists(temp_cache_db, cache_context) -> None:
    """Persistent lifecycle hint should keep data across interpreter instances."""

    call_count = [0]

    @cache(lifecycle=CacheLifecycle.PERSISTENT)
    @do
    def expensive_value() -> EffectGenerator[str]:
        call_count[0] += 1
        return "value"

    engine_one = ProgramInterpreter()
    first = await engine_one.run_async(expensive_value(), cache_context)

    assert first.is_ok
    assert first.value == "value"
    assert call_count[0] == 1
    assert temp_cache_db.exists()

    # Second engine with same cache path via context
    second_context = ExecutionContext(env={CACHE_PATH_ENV_KEY: temp_cache_db})
    engine_two = ProgramInterpreter()
    second = await engine_two.run_async(expensive_value(), second_context)

    assert second.is_ok
    assert second.value == "value"
    # Should still be 1 because cache hit happens in new interpreter
    assert call_count[0] == 1


@pytest.mark.asyncio
async def test_cache_persistent_lifecycle_cross_process(temp_cache_db) -> None:
    """Persistent lifecycle cache survives across different Python processes.

    Cache path is passed via command-line argument and set in context using
    CACHE_PATH_ENV_KEY.
    """

    script = textwrap.dedent(
        """
        import asyncio
        import sys
        from pathlib import Path
        from doeff import (
            CACHE_PATH_ENV_KEY,
            CacheGet,
            CachePut,
            CacheLifecycle,
            ExecutionContext,
            ProgramInterpreter,
            do,
        )

        CACHE_KEY = ("cross_process", 42)

        @do
        def store():
            yield CachePut(CACHE_KEY, "persisted", lifecycle=CacheLifecycle.PERSISTENT)

        @do
        def load():
            return (yield CacheGet(CACHE_KEY))

        async def run(mode: str, cache_path: str) -> None:
            context = ExecutionContext(env={CACHE_PATH_ENV_KEY: Path(cache_path)})
            engine = ProgramInterpreter()
            if mode == "store":
                result = await engine.run_async(store(), context)
                if result.is_err:
                    raise SystemExit(f"store failed: {result.result.error!r}")
            elif mode == "load":
                result = await engine.run_async(load(), context)
                if result.is_err:
                    raise result.result.error
                if result.value != "persisted":
                    raise SystemExit(f"unexpected value: {result.value!r}")
            else:
                raise SystemExit(f"unknown mode: {mode}")

        if __name__ == "__main__":
            asyncio.run(run(sys.argv[1], sys.argv[2]))
        """
    )

    async def run_mode(mode: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            mode,
            str(temp_cache_db),
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
async def test_cache_with_kwargs(temp_cache_db, cache_context) -> None:
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
    result = await engine.run_async(test_program(), cache_context)

    assert result.is_ok
    assert result.value[0] == 6   # 5 + 1
    assert result.value[1] == 6   # Cached
    assert result.value[2] == 15  # 5 * 3
    assert result.value[3] == 6   # Cached
    assert result.value[4] == 2   # Called twice


@pytest.mark.asyncio
async def test_cache_with_ttl(temp_cache_db) -> None:
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
    result = await engine.run_async(test_program())

    assert result.is_ok
    assert result.value[0] == result.value[1]  # Same cached value
    assert result.value[2] > result.value[1]   # New value after expiry
    assert result.value[3] == 2  # Called twice


@pytest.mark.asyncio
async def test_cache_key_selector(temp_cache_db, cache_context) -> None:
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
    result = await engine.run_async(test_program(), cache_context)

    assert result.is_ok
    assert result.value[0] == "User 1"
    assert result.value[1] == "User 1"  # Cached, ignores include_details
    assert result.value[2] == "User 2"
    assert result.value[3] == 2  # Called twice (once for user 1, once for user 2)


@pytest.mark.asyncio
async def test_cache_key_hashers_transform_arguments(temp_cache_db, cache_context) -> None:
    """Test key_hashers applies transformations for positional and keyword args."""

    calls = []

    def dict_hasher(data: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:  # noqa: DOEFF006
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

    payload_one = {"value": 1, "other": 99}
    payload_two = {"other": 99, "value": 1}
    extra_one = {"id": "alpha", "note": "first"}
    extra_two = {"note": "first", "id": "alpha"}

    result1 = await interpreter.run_async(compute(7, payload_one, extra=extra_one), cache_context)
    assert result1.value == 1

    result2 = await interpreter.run_async(
        compute(7, payload_two, extra=extra_two),
        result1.context,
    )
    assert result2.value == 1

    assert calls == [(7, 1)]
    assert hasher_runs == ["alpha", "alpha"]


@pytest.mark.asyncio
async def test_convenience_decorators() -> None:
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
    result = await engine.run_async(test_program())

    assert result.is_ok
    assert result.value == "1min"


@pytest.mark.asyncio
async def test_cache_decorator_hits(temp_cache_db, cache_context) -> None:
    calls = []

    @cache()
    @do
    def compute(x: int) -> EffectGenerator[int]:
        calls.append(x)
        return x * 2

    interpreter = ProgramInterpreter()

    result1 = await interpreter.run_async(compute(3), cache_context)
    assert result1.value == 6
    assert calls == [3]

    result2 = await interpreter.run_async(compute(3), result1.context)
    assert result2.value == 6
    assert calls == [3]


@pytest.mark.asyncio
async def test_cache_decorator_expiry(temp_cache_db, cache_context) -> None:
    calls = []

    @cache(ttl=0.5)
    @do
    def compute(x: int) -> EffectGenerator[int]:
        calls.append(x)
        return x * 2

    interpreter = ProgramInterpreter()
    cache_handler = interpreter.cache_handler
    base_time = 1000.0
    original_time = cache_handler._time

    try:
        cache_handler._time = lambda: base_time
        await interpreter.run_async(compute(5), cache_context)
        assert calls == [5]

        cache_handler._time = lambda: base_time + 0.2
        await interpreter.run_async(compute(5), cache_context)
        assert calls == [5]

        cache_handler._time = lambda: base_time + 1.0
        await interpreter.run_async(compute(5), cache_context)
        assert calls == [5, 5]
    finally:
        cache_handler._time = original_time


@pytest.mark.asyncio
async def test_memo_effects() -> None:
    @do
    def program() -> EffectGenerator[int]:
        yield MemoPut("alpha", 123)
        return (yield MemoGet("alpha"))

    interpreter = ProgramInterpreter()
    result = await interpreter.run_async(program())

    assert result.is_ok
    assert result.value == 123


@pytest.mark.asyncio
async def test_memo_miss_raises_keyerror() -> None:
    @do
    def program() -> EffectGenerator[int]:
        return (yield MemoGet("missing"))

    interpreter = ProgramInterpreter()
    result = await interpreter.run_async(program())

    assert result.is_err
    assert "Memo miss" in str(result.result.error)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


@pytest.mark.asyncio
async def test_cache_decorator_accepts_pil(temp_cache_db, cache_context) -> None:
    from PIL import Image

    calls = []

    @cache()
    @do
    def compute(image: Image.Image) -> EffectGenerator[int]:
        calls.append(image.size)
        return image.width * image.height

    image = Image.new("RGB", (16, 16), color="red")

    interpreter = ProgramInterpreter()

    result1 = await interpreter.run_async(compute(image), cache_context)
    assert result1.value == 256
    assert calls == [(16, 16)]

    result2 = await interpreter.run_async(compute(image), result1.context)
    assert result2.value == 256
    assert calls == [(16, 16)]


# =============================================================================
# Stress tests for concurrent cache access (ISSUE-CORE-416)
# =============================================================================


class TestCacheConcurrentAccess:
    """Stress tests for thread/process safety of CacheEffectHandler.

    These tests verify that the SQLite cache backend works correctly under
    concurrent access from multiple spawned processes and threads.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", ["thread", "process"])
    async def test_parallel_cache_put_different_keys(
        self, temp_cache_db, backend: str
    ) -> None:
        """Multiple parallel spawns all doing cache_put with different keys.

        Each worker writes to a unique key - no contention on data but stress
        tests the SQLite connection under concurrent write load.
        """
        from doeff import Gather, Local, Spawn

        num_workers = 10

        @do
        def worker(worker_id: int) -> EffectGenerator[int]:
            key = f"parallel_key_{worker_id}"
            value = worker_id * 100
            yield CachePut(key, value, lifecycle=CacheLifecycle.PERSISTENT)
            # Read back to verify write succeeded
            result = yield CacheGet(key)
            return result

        @do
        def program() -> EffectGenerator[list[int]]:
            tasks = []
            for i in range(num_workers):
                task = yield Spawn(worker(i), preferred_backend=backend)
                tasks.append(task)
            return (yield Gather(*(t.join() for t in tasks)))

        engine = ProgramInterpreter(spawn_process_max_workers=4)
        # Wrap program in Local to propagate cache path to spawned programs
        result = await engine.run_async(
            Local({CACHE_PATH_ENV_KEY: temp_cache_db}, program())
        )

        assert result.is_ok, f"Expected success, got: {result.result}"
        assert sorted(result.value) == [i * 100 for i in range(num_workers)]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", ["thread", "process"])
    async def test_parallel_cache_put_same_key_race(
        self, temp_cache_db, backend: str
    ) -> None:
        """Multiple parallel spawns all doing cache_get/cache_put with same key.

        This is a race condition stress test - all workers try to update the
        same key. The test verifies no database locked errors occur and that
        one of the values is successfully written.
        """
        from doeff import Gather, Local, Spawn

        num_workers = 8
        shared_key = "race_condition_key"

        @do
        def worker(worker_id: int) -> EffectGenerator[int]:
            value = worker_id * 10
            yield CachePut(shared_key, value, lifecycle=CacheLifecycle.PERSISTENT)
            return worker_id

        @do
        def program() -> EffectGenerator[tuple[list[int], int]]:
            tasks = []
            for i in range(num_workers):
                task = yield Spawn(worker(i), preferred_backend=backend)
                tasks.append(task)
            worker_ids = yield Gather(*(t.join() for t in tasks))
            # Read the final value - one of the workers should have won
            final_value = yield CacheGet(shared_key)
            return worker_ids, final_value

        engine = ProgramInterpreter(spawn_process_max_workers=4)
        result = await engine.run_async(
            Local({CACHE_PATH_ENV_KEY: temp_cache_db}, program())
        )

        assert result.is_ok, f"Expected success, got: {result.result}"
        worker_ids, final_value = result.value
        assert sorted(worker_ids) == list(range(num_workers))
        # Final value should be one of the worker values
        assert final_value in [i * 10 for i in range(num_workers)]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", ["thread", "process"])
    async def test_nested_spawn_cache_access(
        self, temp_cache_db, backend: str
    ) -> None:
        """Nested spawns (spawn within spawn) accessing cache.

        Verifies cache works correctly with deeply nested spawn effects.
        """
        from doeff import Local, Spawn

        @do
        def inner_worker(value: int) -> EffectGenerator[int]:
            key = f"nested_inner_{value}"
            yield CachePut(key, value * 2, lifecycle=CacheLifecycle.PERSISTENT)
            return (yield CacheGet(key))

        @do
        def outer_worker(worker_id: int) -> EffectGenerator[int]:
            # Put a value in cache
            key = f"nested_outer_{worker_id}"
            yield CachePut(key, worker_id, lifecycle=CacheLifecycle.PERSISTENT)

            # Spawn an inner worker (nested spawn)
            inner_task = yield Spawn(inner_worker(worker_id), preferred_backend="thread")
            inner_result = yield inner_task.join()

            # Read our own value back
            outer_result = yield CacheGet(key)
            return outer_result + inner_result

        @do
        def program() -> EffectGenerator[list[int]]:
            from doeff import Gather

            tasks = []
            for i in range(4):
                task = yield Spawn(outer_worker(i), preferred_backend=backend)
                tasks.append(task)
            return (yield Gather(*(t.join() for t in tasks)))

        engine = ProgramInterpreter(spawn_process_max_workers=2)
        result = await engine.run_async(
            Local({CACHE_PATH_ENV_KEY: temp_cache_db}, program())
        )

        assert result.is_ok, f"Expected success, got: {result.result}"
        # Each result should be: worker_id + (worker_id * 2) = worker_id * 3
        assert sorted(result.value) == [0, 3, 6, 9]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", ["thread", "process"])
    async def test_cache_hits_and_misses_concurrent(
        self, temp_cache_db, backend: str
    ) -> None:
        """Mix of cache hits and misses under concurrent load.

        Pre-populates some cache entries, then spawns workers that do a mix
        of reads (hits and misses) and writes.
        """
        from doeff import Gather, Local, Spawn

        # Pre-populate some cache entries
        @do
        def setup() -> EffectGenerator[None]:
            for i in range(5):
                yield CachePut(f"preexisting_{i}", i * 100, lifecycle=CacheLifecycle.PERSISTENT)

        @do
        def worker(worker_id: int) -> EffectGenerator[dict[str, Any]]:
            results: dict[str, Any] = {"worker_id": worker_id, "hits": 0, "misses": 0}

            # Try to read a preexisting key (should hit for worker_id < 5)
            try:
                value = yield CacheGet(f"preexisting_{worker_id}")
                results["hits"] += 1
                results["hit_value"] = value
            except KeyError:
                results["misses"] += 1

            # Write a new key
            yield CachePut(
                f"worker_{worker_id}_result",
                worker_id * 50,
                lifecycle=CacheLifecycle.PERSISTENT,
            )

            # Read back our written key (should always hit)
            try:
                value = yield CacheGet(f"worker_{worker_id}_result")
                results["hits"] += 1
                results["own_value"] = value
            except KeyError:
                results["misses"] += 1
                results["own_value"] = None

            return results

        @do
        def program() -> EffectGenerator[list[dict[str, Any]]]:
            # Setup phase
            yield setup()

            # Concurrent workers
            num_workers = 10
            tasks = []
            for i in range(num_workers):
                task = yield Spawn(worker(i), preferred_backend=backend)
                tasks.append(task)
            return (yield Gather(*(t.join() for t in tasks)))

        engine = ProgramInterpreter(spawn_process_max_workers=4)
        result = await engine.run_async(
            Local({CACHE_PATH_ENV_KEY: temp_cache_db}, program())
        )

        assert result.is_ok, f"Expected success, got: {result.result}"

        for worker_result in result.value:
            worker_id = worker_result["worker_id"]
            # Each worker should have read back their own written value
            assert worker_result["own_value"] == worker_id * 50
            # Workers 0-4 should have cache hits for preexisting keys
            if worker_id < 5:
                assert worker_result["hit_value"] == worker_id * 100

    @pytest.mark.asyncio
    async def test_high_contention_stress(self, temp_cache_db) -> None:
        """High contention stress test with many rapid cache operations.

        Uses thread backend for faster execution but moderate concurrency to stress
        the locking mechanism while avoiding SQLite WAL visibility edge cases.
        """
        from doeff import Gather, Local, Spawn

        num_workers = 10
        ops_per_worker = 5

        @do
        def worker(worker_id: int) -> EffectGenerator[int]:
            total = 0
            for op in range(ops_per_worker):
                key = f"stress_{worker_id}_{op}"
                value = worker_id * 1000 + op
                yield CachePut(key, value, lifecycle=CacheLifecycle.PERSISTENT)
                result = yield CacheGet(key)
                total += result
            return total

        @do
        def program() -> EffectGenerator[list[int]]:
            tasks = []
            for i in range(num_workers):
                task = yield Spawn(worker(i), preferred_backend="thread")
                tasks.append(task)
            return (yield Gather(*(t.join() for t in tasks)))

        engine = ProgramInterpreter()
        result = await engine.run_async(
            Local({CACHE_PATH_ENV_KEY: temp_cache_db}, program())
        )

        assert result.is_ok, f"Expected success, got: {result.result}"

        # Verify each worker got their expected sum
        for worker_id in range(num_workers):
            expected_sum = sum(worker_id * 1000 + op for op in range(ops_per_worker))
            assert expected_sum in result.value

    @pytest.mark.asyncio
    async def test_process_backend_wal_mode_enabled(self, temp_cache_db, cache_context) -> None:
        """Verify WAL mode is enabled on the database.

        This test directly checks the SQLite database to confirm WAL mode
        was activated by the CacheEffectHandler.
        """
        # First, do a cache operation to create the database
        @do
        def setup() -> EffectGenerator[None]:
            yield CachePut("wal_test_key", "wal_test_value", lifecycle=CacheLifecycle.PERSISTENT)

        engine = ProgramInterpreter()
        result = await engine.run_async(setup(), cache_context)
        assert result.is_ok

        # Now check the journal mode directly
        import sqlite3

        with sqlite3.connect(temp_cache_db) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal", f"Expected WAL mode, got {mode}"
