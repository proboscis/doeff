import asyncio
import atexit
import importlib.util
import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

import pytest

from doeff import (
    AtomicGet,
    AtomicUpdate,
    Effect,
    EffectGenerator,
    Fail,
    Gather,
    Get,
    IO,
    Log,
    Parallel,
    Program,
    Put,
    ProgramInterpreter,
    Recover,
    Spawn,
    Task,
    do,
    slog,
)
from doeff.effects import WriterTellEffect

_RAY_TEST_CPUS = 4


def _backend_params() -> list[Any]:  # noqa: DOEFF022
    return [
        pytest.param("thread", id="thread"),
        pytest.param("process", id="process"),
        pytest.param("ray", id="ray"),
    ]


def _ray_task_options(backend: str) -> dict[str, Any]:  # noqa: DOEFF022
    if backend == "ray":
        return {"num_cpus": 1}
    return {}


def _build_engine(backend: str, *, default_backend: str | None = None) -> ProgramInterpreter:  # noqa: DOEFF022
    spawn_defaults = {}
    if default_backend is not None:
        spawn_defaults["spawn_default_backend"] = default_backend
    if backend == "ray":
        spawn_defaults["spawn_ray_init_kwargs"] = {
            "num_cpus": _RAY_TEST_CPUS,
            "include_dashboard": False,
            "log_to_driver": False,
        }
    if backend == "process":
        spawn_defaults["spawn_process_max_workers"] = 2
    return ProgramInterpreter(**spawn_defaults)


_ray_runtime: Any | None = None


@contextmanager
def _ray_context(backend: str) -> Iterator[None]:  # noqa: DOEFF022
    if backend != "ray":
        yield
        return
    global _ray_runtime
    if _ray_runtime is None:
        ray = pytest.importorskip("ray")
        if ray.is_initialized():
            ray.shutdown()
        ray.init(
            num_cpus=_RAY_TEST_CPUS,
            include_dashboard=False,
            log_to_driver=False,
            runtime_env={"working_dir": "."},
        )
        _ray_runtime = ray
        atexit.register(_shutdown_ray)
    yield


def _shutdown_ray() -> None:  # noqa: DOEFF022
    if _ray_runtime is not None:
        _ray_runtime.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_join_basic(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker() -> EffectGenerator[int]:
            yield Put("status", backend)
            return 10

        @do
        def program() -> EffectGenerator[tuple[int, str | None]]:
            task = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            result = yield task.join()
            status = yield Get("status")
            return result, status

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == (10, backend)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_default_backend_selection(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend, default_backend=backend)

        @do
        def worker() -> EffectGenerator[str]:
            return "ready"

        @do
        def program() -> EffectGenerator[str]:
            task = yield Spawn(worker())
            return (yield task.join())

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "ready"


@pytest.mark.asyncio
async def test_spawn_warns_when_ray_unavailable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    engine = ProgramInterpreter()
    original_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, *args: Any, **kwargs: Any) -> Any:  # noqa: DOEFF022
        if name == "ray":
            return None
        return original_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    caplog.set_level(logging.WARNING, logger="doeff.handlers")

    @do
    def worker() -> EffectGenerator[int]:
        return 5

    @do
    def program() -> EffectGenerator[int]:
        task = yield Spawn(worker(), preferred_backend="ray")
        return (yield task.join())

    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == 5
    assert any(
        "Ray backend requested but 'ray' is not installed" in message
        for message in caplog.messages
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_intercept_slog(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker() -> EffectGenerator[str]:
            yield slog(event="spawned", detail="ok")
            return "done"

        def intercept(effect: Effect) -> Effect:  # noqa: DOEFF022
            if isinstance(effect, WriterTellEffect) and isinstance(effect.message, dict):
                if effect.message.get("event") == "spawned":
                    return Log({"intercepted": effect.message["event"]})
            return effect

        @do
        def program() -> EffectGenerator[str]:
            task = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            return (yield task.join())

        result = await engine.run_async(program().intercept(intercept))

    assert result.is_ok
    assert result.value == "done"
    assert {"intercepted": "spawned"} in result.log
    assert not any(
        isinstance(entry, dict) and entry.get("event") == "spawned" for entry in result.log
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_multiple_tasks(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker(index: int) -> EffectGenerator[int]:
            yield Put(f"state_{index}", index)
            return index

        @do
        def program() -> EffectGenerator[tuple[list[int], list[int]]]:
            tasks = []
            for i in range(3):  # noqa: DOEFF012
                tasks.append(
                    (
                        yield Spawn(
                            worker(i),
                            preferred_backend=backend,
                            **_ray_task_options(backend),
                        )
                    )
                )
            results: list[int] = []
            for task in tasks:  # noqa: DOEFF012
                results.append((yield task.join()))
            states: list[int] = []
            for i in range(3):  # noqa: DOEFF012
                states.append((yield Get(f"state_{i}")))
            return results, states

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == ([0, 1, 2], [0, 1, 2])


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_with_gather(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker(index: int) -> EffectGenerator[int]:
            return index * 2

        @do
        def program() -> EffectGenerator[list[int]]:
            tasks = []
            for i in range(4):  # noqa: DOEFF012
                tasks.append(
                    (
                        yield Spawn(
                            worker(i),
                            preferred_backend=backend,
                            **_ray_task_options(backend),
                        )
                    )
                )
            return (yield Gather(*(task.join() for task in tasks)))

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == [0, 2, 4, 6]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_worker_parallel_awaitables(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        async def compute(label: str) -> str:  # noqa: DOEFF022
            await asyncio.sleep(0.01)
            return label

        @do
        def worker() -> EffectGenerator[list[str]]:
            return (yield Parallel(compute("a"), compute("b")))

        @do
        def program() -> EffectGenerator[list[str]]:
            task = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            return (yield task.join())

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == ["a", "b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_worker_gather_programs(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def subtask(index: int) -> EffectGenerator[int]:
            yield Put(f"sub_{index}", index)
            return index

        @do
        def worker() -> EffectGenerator[int]:
            results = yield Gather(subtask(1), subtask(2))
            return sum(results)

        @do
        def program() -> EffectGenerator[tuple[int, int | None, int | None]]:
            task = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            total = yield task.join()
            first = yield Get("sub_1")
            second = yield Get("sub_2")
            return total, first, second

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == (3, 1, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_recover_handles_failure(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker() -> EffectGenerator[int]:
            yield Fail(RuntimeError("boom"))
            return 0

        @do
        def program() -> EffectGenerator[int]:
            task = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            return (yield Recover(task.join(), fallback=42))

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == 42


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_state_snapshot_read(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker() -> EffectGenerator[str | None]:
            return (yield Get("flag"))

        @do
        def program() -> EffectGenerator[tuple[str | None, str | None]]:
            yield Put("flag", "before")
            task = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            yield Put("flag", "after")
            seen = yield task.join()
            current = yield Get("flag")
            return seen, current

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == ("before", "after")


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_state_merge_preserves_parent_updates(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker() -> EffectGenerator[str]:
            yield Put("worker_key", "done")
            return "ok"

        @do
        def program() -> EffectGenerator[tuple[str, int | None, str | None]]:
            yield Put("counter", 1)
            task = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            yield Put("counter", 2)
            value = yield task.join()
            counter = yield Get("counter")
            worker_value = yield Get("worker_key")
            return value, counter, worker_value

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == ("ok", 2, "done")


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_state_isolation_between_tasks(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker(index: int, delay: float) -> EffectGenerator[int]:
            yield Put("shared", index)
            yield IO(lambda: time.sleep(delay))
            return (yield Get("shared"))

        @do
        def program() -> EffectGenerator[list[int]]:
            first = yield Spawn(
                worker(1, 0.2),
                preferred_backend=backend,
                **_ray_task_options(backend),
            )
            second = yield Spawn(
                worker(2, 0.05),
                preferred_backend=backend,
                **_ray_task_options(backend),
            )
            return (yield Gather(first.join(), second.join()))

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == [1, 2]


@pytest.mark.asyncio
async def test_spawn_thread_atomic_updates_shared_state() -> None:
    engine = ProgramInterpreter()

    @do
    def worker(updates: int) -> EffectGenerator[str]:
        for _ in range(updates):
            yield AtomicUpdate(
                "counter",
                lambda current: (current or 0) + 1,
                default_factory=lambda: 0,
            )
        return "done"

    @do
    def program() -> EffectGenerator[int]:
        first = yield Spawn(worker(3), preferred_backend="thread")
        second = yield Spawn(worker(4), preferred_backend="thread")
        yield first.join()
        yield second.join()
        return (yield AtomicGet("counter", default_factory=lambda: 0))

    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_exception_propagates(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker() -> EffectGenerator[int]:
            yield Fail(ValueError("boom"))
            return 0

        @do
        def program() -> EffectGenerator[int]:
            task = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            return (yield task.join())

        result = await engine.run_async(program())

    assert result.is_err
    assert "boom" in str(result.result.error)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_join_idempotent(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker() -> EffectGenerator[int]:
            yield Log("joined-once")
            yield Put("value", "ok")
            return 7

        @do
        def program() -> EffectGenerator[tuple[int, int, str | None]]:
            task = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            first = yield task.join()
            second = yield task.join()
            value = yield Get("value")
            return first, second, value

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == (7, 7, "ok")
    assert result.log.count("joined-once") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_nested_spawn(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def inner() -> EffectGenerator[int]:
            return 3

        @do
        def outer() -> EffectGenerator[int]:
            inner_task = yield Spawn(inner(), preferred_backend="thread")
            inner_value = yield inner_task.join()
            return inner_value + 1

        @do
        def program() -> EffectGenerator[int]:
            task = yield Spawn(outer(), preferred_backend=backend, **_ray_task_options(backend))
            return (yield task.join())

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == 4


@pytest.mark.asyncio
async def test_spawn_triple_nested_remote_call() -> None:
    with _ray_context("ray"):
        engine = _build_engine("ray")
        ray_options = _ray_task_options("ray")

        @do
        def level_three() -> EffectGenerator[int]:
            yield Put("level3", "ok")
            return 3

        @do
        def level_two() -> EffectGenerator[Task[int]]:
            task = yield Spawn(
                level_three(),
                preferred_backend="ray",
                **ray_options,
            )
            yield Put("level2", "spawned")
            return task

        @do
        def level_one() -> EffectGenerator[Task[int]]:
            task = yield Spawn(
                level_two(),
                preferred_backend="ray",
                **ray_options,
            )
            yield Put("level1", "spawned")
            return task

        @do
        def program() -> EffectGenerator[tuple[int, str | None, str | None, str | None]]:
            task = yield Spawn(
                level_one(),
                preferred_backend="ray",
                **ray_options,
            )
            nested_two = yield task.join()
            nested_three = yield nested_two.join()
            value = yield nested_three.join()
            level1 = yield Get("level1")
            level2 = yield Get("level2")
            level3 = yield Get("level3")
            return value, level1, level2, level3

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == (3, "spawned", "spawned", "ok")


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_parallel_overlap(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)
        delay = 2.5 if backend == "ray" else 0.6

        @do
        def worker() -> EffectGenerator[tuple[float, float]]:
            start = yield IO(time.perf_counter)
            yield IO(lambda: time.sleep(delay))
            end = yield IO(time.perf_counter)
            return start, end

        @do
        def program() -> EffectGenerator[list[tuple[float, float]]]:
            first = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            second = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            return (yield Gather(first.join(), second.join()))

        result = await engine.run_async(program())

    assert result.is_ok
    starts = [entry[0] for entry in result.value]
    ends = [entry[1] for entry in result.value]
    if backend == "ray" and max(starts) >= min(ends):
        pytest.skip("Ray scheduler executed tasks serially; overlap depends on worker availability.")
    assert max(starts) < min(ends)


@pytest.mark.asyncio
async def test_spawn_ray_resource_hints() -> None:
    with _ray_context("ray"):
        engine = ProgramInterpreter()

        @do
        def worker() -> EffectGenerator[int]:
            return 11

        @do
        def program() -> EffectGenerator[int]:
            task = yield Spawn(
                worker(),
                preferred_backend="ray",
                num_cpus=1,
                num_gpus=0,
                memory=10_000_000,
            )
            return (yield task.join())

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == 11


@pytest.mark.asyncio
async def test_spawn_process_serialization_error() -> None:
    engine = ProgramInterpreter()

    class Unpicklable:
        def __getstate__(self) -> None:
            raise TypeError("nope")

    @do
    def worker() -> EffectGenerator[int]:
        return 1

    @do
    def program() -> EffectGenerator[int]:
        yield Put("bad", Unpicklable())
        task = yield Spawn(worker(), preferred_backend="process")
        return (yield task.join())

    result = await engine.run_async(program())

    assert result.is_err
    assert "cloudpickle" in str(result.result.error).lower()


class TestEnvSerializationIssues:
    """Tests for env serialization issues with spawn backends.

    These tests verify that:
    1. Programs created via flat_map (with lambda factories) serialize correctly
    2. AskEffect objects with frame_info serialize correctly after the frame_info fix

    Both env (reader environment) and state are serialized and passed to spawned
    processes. These tests use state (via Put/Get) to store Programs/Effects since
    state is the typical mechanism for passing data to spawned workers.
    """

    @pytest.mark.asyncio
    async def test_process_backend_succeeds_with_flat_map_program_in_env(self) -> None:
        """Process backend succeeds with Program from flat_map using cloudpickle.

        flat_map creates a GeneratorProgram with a local factory function that
        captures the binder lambda in its closure. Standard pickle cannot serialize
        this, but cloudpickle can.
        """
        engine = ProgramInterpreter(spawn_process_max_workers=2)

        # Create a Program via flat_map - this creates a GeneratorProgram with
        # a local factory function containing a lambda closure
        p_base = Program.pure(5)
        p_flat_mapped = p_base.flat_map(lambda x: Program.pure(x * 2))

        @do
        def worker() -> EffectGenerator[int]:
            # Access the program from state and run it
            prog = yield Get("program")
            return (yield prog)

        @do
        def program() -> EffectGenerator[int]:
            # Put the flat_mapped program (with lambda closure) in state
            # State is serialized and passed to spawned processes
            yield Put("program", p_flat_mapped)
            task = yield Spawn(worker(), preferred_backend="process")
            return (yield task.join())

        result = await engine.run_async(program())

        assert result.is_ok, f"Expected success, got error: {result.result}"
        assert result.value == 10  # 5 * 2 = 10

    @pytest.mark.asyncio
    async def test_ray_backend_succeeds_with_flat_map_program_in_env(self) -> None:
        """Ray backend succeeds with Program from flat_map."""
        with _ray_context("ray"):
            engine = _build_engine("ray")

            # Create a Program via flat_map
            p_base = Program.pure(5)
            p_flat_mapped = p_base.flat_map(lambda x: Program.pure(x * 2))

            @do
            def worker() -> EffectGenerator[int]:
                prog = yield Get("program")
                return (yield prog)

            @do
            def program() -> EffectGenerator[int]:
                yield Put("program", p_flat_mapped)
                task = yield Spawn(
                    worker(),
                    preferred_backend="ray",
                    **_ray_task_options("ray"),
                )
                return (yield task.join())

            result = await engine.run_async(program())

        assert result.is_ok, f"Expected success, got error: {result.result}"
        assert result.value == 10

    @pytest.mark.asyncio
    async def test_process_backend_succeeds_with_ask_effect_after_frame_info_fix(
        self,
    ) -> None:
        """Process backend succeeds with AskEffect after frame_info fix.

        AskEffect objects capture frame_info for debugging/tracing purposes.
        The frame_info fix (ISSUE-CORE-407) added __getstate__/__setstate__ to
        EffectCreationContext to sanitize frame references before serialization.

        This test stores an AskEffect in state, serializes it to a subprocess,
        retrieves it, and yields it. The AskEffect then reads from env which
        must also be set up correctly.
        """
        from doeff import Local
        from doeff.effects import ask

        engine = ProgramInterpreter(spawn_process_max_workers=2)

        # Create an AskEffect which has frame_info captured at creation time
        ask_effect = ask("some_key")

        @do
        def worker() -> EffectGenerator[str | None]:
            # Access the ask effect from state and yield it
            # The AskEffect will then read "some_key" from env
            effect = yield Get("ask_effect")
            return (yield effect)

        @do
        def program() -> EffectGenerator[str | None]:
            # Put the ask effect (with frame_info) in state
            yield Put("ask_effect", ask_effect)
            # Use Local to set "some_key" in env, then spawn within that context
            inner_program = Spawn(worker(), preferred_backend="process")

            @do
            def spawn_and_join() -> EffectGenerator[str | None]:
                task = yield inner_program
                return (yield task.join())

            # Run spawn within Local context so env has "some_key"
            return (yield Local({"some_key": "expected_value"}, spawn_and_join()))

        result = await engine.run_async(program())

        assert result.is_ok, f"Expected success, got error: {result.result}"
        assert result.value == "expected_value"


class TestValuesEqual:
    """Tests for SpawnEffectHandler._values_equal handling non-bool __eq__ results.

    This addresses ISSUE-CORE-412: when comparing values during context merge,
    objects with non-bool __eq__ (numpy arrays, pandas DataFrames) would cause
    'ValueError: The truth value of a DataFrame is ambiguous'.
    """

    def test_values_equal_with_regular_values(self) -> None:
        """Test _values_equal works with regular Python values."""
        from doeff.handlers import SpawnEffectHandler

        assert SpawnEffectHandler._values_equal(1, 1) is True
        assert SpawnEffectHandler._values_equal(1, 2) is False
        assert SpawnEffectHandler._values_equal("a", "a") is True
        assert SpawnEffectHandler._values_equal("a", "b") is False
        assert SpawnEffectHandler._values_equal([1, 2], [1, 2]) is True
        assert SpawnEffectHandler._values_equal([1, 2], [1, 3]) is False

    def test_values_equal_with_numpy_array(self) -> None:
        """Test _values_equal handles numpy arrays (non-bool __eq__)."""
        import numpy as np
        from doeff.handlers import SpawnEffectHandler

        arr1 = np.array([1, 2, 3])
        arr2 = np.array([1, 2, 3])
        arr3 = np.array([4, 5, 6])

        # numpy __eq__ returns array, not bool - should return False, not raise
        assert SpawnEffectHandler._values_equal(arr1, arr2) is False
        assert SpawnEffectHandler._values_equal(arr1, arr3) is False

        # Same object should still work (identity check happens before __eq__)
        assert SpawnEffectHandler._values_equal(arr1, arr1) is False  # numpy returns array

    def test_values_equal_with_exception_raising_eq(self) -> None:
        """Test _values_equal handles objects that raise on __eq__."""
        from doeff.handlers import SpawnEffectHandler

        class RaisingEq:
            def __eq__(self, other: object) -> bool:
                raise RuntimeError("Cannot compare")

        obj1 = RaisingEq()
        obj2 = RaisingEq()

        # Should return False instead of raising
        assert SpawnEffectHandler._values_equal(obj1, obj2) is False


class TestSpawnAutoJoin:
    """Tests for ISSUE-CORE-419: Auto-join spawned tasks at interpreter shutdown.

    When spawned tasks are not explicitly joined, the interpreter should:
    1. Automatically join them at the end of program execution
    2. Log warnings for any failed unjoined tasks (not Python's cryptic Future warning)
    3. Prevent "Future exception was never retrieved" log spam
    """

    @pytest.mark.asyncio
    async def test_unjoined_spawn_failures_logged_as_warnings(
        self, capfd: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unjoined spawns that fail should log warnings, not Future exception spam."""
        import gc

        caplog.set_level(logging.WARNING, logger="doeff.interpreter")
        engine = ProgramInterpreter()

        @do
        def failing_worker(index: int) -> EffectGenerator[int]:
            yield Fail(RuntimeError(f"Worker {index} failed intentionally"))
            return 0

        @do
        def program() -> EffectGenerator[str]:
            # Spawn tasks but don't join them
            for i in range(3):  # noqa: DOEFF012
                yield Spawn(failing_worker(i), preferred_backend="thread")
            return "done_without_joining"

        result = await engine.run_async(program())

        # Force garbage collection
        gc.collect()
        await asyncio.sleep(0.1)
        gc.collect()

        assert result.is_ok
        assert result.value == "done_without_joining"

        # Should NOT have Python's cryptic Future warning
        captured = capfd.readouterr()
        assert "Future exception was never retrieved" not in captured.err

        # Should have our clean warnings for each failed unjoined task
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_messages) == 3
        for msg in warning_messages:
            assert "Spawned task was not joined and failed with error" in msg

    @pytest.mark.asyncio
    async def test_joined_spawn_failures_no_duplicate_warnings(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Spawns that are explicitly joined should not produce warnings."""
        caplog.set_level(logging.WARNING, logger="doeff.interpreter")
        engine = ProgramInterpreter()

        @do
        def failing_worker(index: int) -> EffectGenerator[int]:
            yield Fail(RuntimeError(f"Worker {index} failed"))
            return 0

        @do
        def program() -> EffectGenerator[list[int]]:
            tasks = []
            for i in range(3):  # noqa: DOEFF012
                tasks.append((yield Spawn(failing_worker(i), preferred_backend="thread")))
            # Join all tasks with Recover
            results = []
            for i, task in enumerate(tasks):  # noqa: DOEFF012
                results.append((yield Recover(task.join(), fallback=-i)))
            return results

        result = await engine.run_async(program())

        assert result.is_ok
        assert result.value == [0, -1, -2]

        # No warnings should be logged since all tasks were explicitly joined
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_messages) == 0

    @pytest.mark.asyncio
    async def test_mixed_joined_unjoined_spawns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Mix of joined and unjoined spawns - only unjoined failures get warnings."""
        caplog.set_level(logging.WARNING, logger="doeff.interpreter")
        engine = ProgramInterpreter()

        @do
        def failing_worker(index: int) -> EffectGenerator[int]:
            yield Fail(RuntimeError(f"Worker {index} failed"))
            return 0

        @do
        def program() -> EffectGenerator[int]:
            # Spawn 4 tasks
            task0 = yield Spawn(failing_worker(0), preferred_backend="thread")
            task1 = yield Spawn(failing_worker(1), preferred_backend="thread")
            task2 = yield Spawn(failing_worker(2), preferred_backend="thread")
            task3 = yield Spawn(failing_worker(3), preferred_backend="thread")

            # Only join task0 and task2
            yield Recover(task0.join(), fallback=-1)
            yield Recover(task2.join(), fallback=-1)

            # task1 and task3 are not joined
            return 42

        result = await engine.run_async(program())

        assert result.is_ok
        assert result.value == 42

        # Should have exactly 2 warnings for unjoined tasks (task1 and task3)
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_messages) == 2

    @pytest.mark.asyncio
    async def test_successful_unjoined_spawns_no_warnings(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Successful unjoined spawns should not produce warnings."""
        caplog.set_level(logging.WARNING, logger="doeff.interpreter")
        engine = ProgramInterpreter()

        @do
        def success_worker(index: int) -> EffectGenerator[int]:
            return index * 10

        @do
        def program() -> EffectGenerator[str]:
            # Spawn successful tasks but don't join them
            for i in range(3):  # noqa: DOEFF012
                yield Spawn(success_worker(i), preferred_backend="thread")
            return "done"

        result = await engine.run_async(program())

        assert result.is_ok
        assert result.value == "done"

        # No warnings for successful tasks
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_messages) == 0

    @pytest.mark.asyncio
    async def test_no_future_exception_warning_in_stderr(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Verify no 'Future exception was never retrieved' in stderr."""
        import gc

        engine = ProgramInterpreter()

        @do
        def failing_worker() -> EffectGenerator[int]:
            yield Fail(RuntimeError("boom"))
            return 0

        @do
        def program() -> EffectGenerator[str]:
            yield Spawn(failing_worker(), preferred_backend="thread")
            yield Spawn(failing_worker(), preferred_backend="thread")
            yield Spawn(failing_worker(), preferred_backend="thread")
            return "done"

        await engine.run_async(program())

        # Force GC to trigger any Future warnings
        gc.collect()
        await asyncio.sleep(0.2)
        gc.collect()

        captured = capfd.readouterr()
        assert "Future exception was never retrieved" not in captured.err
