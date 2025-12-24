import asyncio
import atexit
import importlib.util
import logging
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

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


class TestInterceptSideEffectsInSpawn:
    """Tests for interceptor side effects in spawned programs (ISSUE-CORE-413).

    These tests verify that interceptors with side effects are actually called
    when effects are yielded from spawned subprograms. The key difference from
    test_spawn_intercept_slog is that these tests check side effects are executed,
    not just that the effect is transformed.
    """

    @pytest.mark.asyncio
    async def test_thread_intercept_side_effect_called(self) -> None:
        """Thread backend: interceptor side effect should be called in same process."""
        engine = ProgramInterpreter()
        side_effects: list[dict[str, Any]] = []

        @do
        def worker() -> EffectGenerator[str]:
            yield slog(event="worker_log", source="worker")
            return "done"

        def intercept(effect: Effect) -> Effect:
            if isinstance(effect, WriterTellEffect) and isinstance(effect.message, dict):
                side_effects.append(effect.message.copy())  # Side effect!
            return effect

        @do
        def program() -> EffectGenerator[str]:
            task = yield Spawn(worker(), preferred_backend="thread")
            return (yield task.join())

        result = await engine.run_async(program().intercept(intercept))

        assert result.is_ok
        assert result.value == "done"
        # The interceptor side effect should have been called
        assert len(side_effects) == 1
        assert side_effects[0] == {"event": "worker_log", "source": "worker"}
        # The log should also be in the result
        assert {"event": "worker_log", "source": "worker"} in result.log

    @pytest.mark.asyncio
    async def test_thread_intercept_called_for_all_worker_effects(self) -> None:
        """Thread backend: interceptor should be called for ALL effects from worker."""
        engine = ProgramInterpreter()
        intercepted_count = [0]  # Use list for mutability in closure

        @do
        def worker() -> EffectGenerator[str]:
            yield slog(step=1)
            yield slog(step=2)
            yield slog(step=3)
            return "done"

        def intercept(effect: Effect) -> Effect:
            if isinstance(effect, WriterTellEffect):
                intercepted_count[0] += 1
            return effect

        @do
        def program() -> EffectGenerator[str]:
            task = yield Spawn(worker(), preferred_backend="thread")
            return (yield task.join())

        result = await engine.run_async(program().intercept(intercept))

        assert result.is_ok
        # Should have intercepted all 3 slog effects from worker
        assert intercepted_count[0] == 3

    @pytest.mark.asyncio
    async def test_thread_intercept_nested_spawn_both_intercepted(self) -> None:
        """Thread backend: interceptor should be called for nested spawns too."""
        engine = ProgramInterpreter()
        intercepted_sources: list[str] = []

        @do
        def inner_worker() -> EffectGenerator[str]:
            yield slog(source="inner")
            return "inner_done"

        @do
        def outer_worker() -> EffectGenerator[str]:
            yield slog(source="outer_before")
            inner_task = yield Spawn(inner_worker(), preferred_backend="thread")
            inner_result = yield inner_task.join()
            yield slog(source="outer_after")
            return f"outer_done_{inner_result}"

        def intercept(effect: Effect) -> Effect:
            if isinstance(effect, WriterTellEffect) and isinstance(effect.message, dict):
                source = effect.message.get("source")
                if source:
                    intercepted_sources.append(source)
            return effect

        @do
        def program() -> EffectGenerator[str]:
            task = yield Spawn(outer_worker(), preferred_backend="thread")
            return (yield task.join())

        result = await engine.run_async(program().intercept(intercept))

        assert result.is_ok
        assert result.value == "outer_done_inner_done"
        # All sources should have been intercepted
        assert "outer_before" in intercepted_sources
        assert "inner" in intercepted_sources
        assert "outer_after" in intercepted_sources

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", _backend_params())
    async def test_intercept_transforms_effect_in_spawn(self, backend: str) -> None:
        """Verify interceptor transforms are applied to effects in spawned programs."""
        with _ray_context(backend):
            engine = _build_engine(backend)

            @do
            def worker() -> EffectGenerator[str]:
                yield slog(event="original")
                return "done"

            def intercept(effect: Effect) -> Effect:
                if isinstance(effect, WriterTellEffect) and isinstance(effect.message, dict):
                    if effect.message.get("event") == "original":
                        # Transform the effect
                        return Log({"event": "transformed", "backend": backend})
                return effect

            @do
            def program() -> EffectGenerator[str]:
                task = yield Spawn(
                    worker(),
                    preferred_backend=backend,
                    **_ray_task_options(backend),
                )
                return (yield task.join())

            result = await engine.run_async(program().intercept(intercept))

        assert result.is_ok
        assert result.value == "done"
        # The transformed log should be present
        assert {"event": "transformed", "backend": backend} in result.log
        # The original log should NOT be present
        assert not any(
            isinstance(entry, dict) and entry.get("event") == "original"
            for entry in result.log
        )

    @pytest.mark.asyncio
    async def test_thread_intercept_with_import_inside(self) -> None:
        """Test interceptor that imports modules inside (like loguru_interceptor)."""
        engine = ProgramInterpreter()
        log_records: list[str] = []

        @do
        def worker() -> EffectGenerator[str]:
            yield slog(message="test message", level="INFO")
            return "done"

        def loguru_style_interceptor(e: Effect) -> Effect:
            # This mimics the loguru_interceptor pattern:
            # - Import inside the function
            # - Perform side effect
            # - Return effect unchanged
            import logging
            local_logger = logging.getLogger("test_interceptor")

            if isinstance(e, WriterTellEffect) and isinstance(e.message, dict):
                msg = e.message.get("message", "")
                log_records.append(f"intercepted: {msg}")
            return e

        @do
        def program() -> EffectGenerator[str]:
            task = yield Spawn(worker(), preferred_backend="thread")
            return (yield task.join())

        result = await engine.run_async(program().intercept(loguru_style_interceptor))

        assert result.is_ok
        assert result.value == "done"
        # The interceptor should have been called
        assert len(log_records) == 1
        assert log_records[0] == "intercepted: test message"

    @pytest.mark.asyncio
    async def test_intercept_with_closure_variable(self) -> None:
        """Test interceptor captures closure variables correctly."""
        engine = ProgramInterpreter()
        captured_prefix = "CAPTURED"
        intercepted_messages: list[str] = []

        @do
        def worker() -> EffectGenerator[str]:
            yield slog(msg="hello")
            yield slog(msg="world")
            return "done"

        def make_interceptor(prefix: str) -> Callable[[Effect], Effect]:
            def interceptor(e: Effect) -> Effect:
                if isinstance(e, WriterTellEffect) and isinstance(e.message, dict):
                    msg = e.message.get("msg", "")
                    intercepted_messages.append(f"{prefix}:{msg}")
                return e
            return interceptor

        @do
        def program() -> EffectGenerator[str]:
            task = yield Spawn(worker(), preferred_backend="thread")
            return (yield task.join())

        result = await engine.run_async(
            program().intercept(make_interceptor(captured_prefix))
        )

        assert result.is_ok
        # Both messages should have been intercepted with the captured prefix
        assert "CAPTURED:hello" in intercepted_messages
        assert "CAPTURED:world" in intercepted_messages

    @pytest.mark.asyncio
    async def test_process_intercept_transforms_apply(self) -> None:
        """Process backend: verify transforms are applied even if side effects aren't visible."""
        engine = ProgramInterpreter(spawn_process_max_workers=2)

        @do
        def worker() -> EffectGenerator[str]:
            yield slog(original=True, value=42)
            return "done"

        def transform_interceptor(e: Effect) -> Effect:
            # This transforms the effect - should work even in process backend
            if isinstance(e, WriterTellEffect) and isinstance(e.message, dict):
                if e.message.get("original"):
                    return Log({"transformed": True, "value": e.message["value"] * 2})
            return e

        @do
        def program() -> EffectGenerator[str]:
            task = yield Spawn(worker(), preferred_backend="process")
            return (yield task.join())

        result = await engine.run_async(program().intercept(transform_interceptor))

        assert result.is_ok
        assert result.value == "done"
        # The transformed log should appear
        assert {"transformed": True, "value": 84} in result.log
        # Original should not appear
        assert not any(
            isinstance(e, dict) and e.get("original") for e in result.log
        )
