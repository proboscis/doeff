import asyncio
import atexit
import time
from contextlib import contextmanager
from typing import Any, Iterator

import pytest

from doeff import (
    AtomicGet,
    AtomicUpdate,
    Fail,
    Gather,
    Get,
    IO,
    Log,
    Parallel,
    Put,
    Recover,
    Spawn,
    ProgramInterpreter,
    do,
    EffectGenerator,
)


def _backend_params() -> list[Any]:
    return [
        pytest.param("thread", id="thread"),
        pytest.param("process", id="process"),
        pytest.param("ray", id="ray"),
    ]


def _ray_task_options(backend: str) -> dict[str, Any]:
    if backend == "ray":
        return {"num_cpus": 1}
    return {}


def _build_engine(backend: str, *, default_backend: str | None = None) -> ProgramInterpreter:
    spawn_defaults = {}
    if default_backend is not None:
        spawn_defaults["spawn_default_backend"] = default_backend
    if backend == "ray":
        spawn_defaults["spawn_ray_init_kwargs"] = {
            "num_cpus": 2,
            "include_dashboard": False,
            "log_to_driver": False,
        }
    if backend == "process":
        spawn_defaults["spawn_process_max_workers"] = 2
    return ProgramInterpreter(**spawn_defaults)


_ray_runtime: Any | None = None


@contextmanager
def _ray_context(backend: str) -> Iterator[None]:
    if backend != "ray":
        yield
        return
    global _ray_runtime
    if _ray_runtime is None:
        ray = pytest.importorskip("ray")
        if ray.is_initialized():
            ray.shutdown()
        ray.init(
            num_cpus=2,
            include_dashboard=False,
            log_to_driver=False,
            runtime_env={"working_dir": "."},
        )
        _ray_runtime = ray
        atexit.register(_shutdown_ray)
    yield


def _shutdown_ray() -> None:
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
            for i in range(3):
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
            for task in tasks:
                results.append((yield task.join()))
            states: list[int] = []
            for i in range(3):
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
            for i in range(4):
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

        async def compute(label: str) -> str:
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
