import time
from typing import Any

import pytest

from doeff import (
    Gather,
    Get,
    IO,
    Put,
    Spawn,
    ProgramInterpreter,
    do,
    EffectGenerator,
)


@pytest.mark.asyncio
async def test_spawn_thread_join_updates_state() -> None:
    engine = ProgramInterpreter()

    @do
    def worker() -> EffectGenerator[int]:
        yield Put("status", "ready")
        return 10

    @do
    def program() -> EffectGenerator[tuple[int, str | None]]:
        task = yield Spawn(worker(), preferred_backend="thread")
        result = yield task.join()
        status = yield Get("status")
        return result, status

    result = await engine.run_async(program())
    assert result.is_ok
    assert result.value == (10, "ready")


@pytest.fixture(scope="module")
def ray_runtime() -> Any:
    ray = pytest.importorskip("ray")
    ray.shutdown()
    yield ray
    ray.shutdown()


@pytest.mark.asyncio
async def test_spawn_ray_join_returns_value(ray_runtime: Any) -> None:
    engine = ProgramInterpreter(spawn_ray_init_kwargs={"num_cpus": 2})

    @do
    def worker() -> EffectGenerator[int]:
        yield Put("ray_value", 42)
        return 5

    @do
    def program() -> EffectGenerator[tuple[int, int | None]]:
        task = yield Spawn(worker(), preferred_backend="ray", num_cpus=1)
        result = yield task.join()
        stored = yield Get("ray_value")
        return result, stored

    result = await engine.run_async(program())
    assert result.is_ok
    assert result.value == (5, 42)


@pytest.mark.asyncio
async def test_spawn_ray_tasks_overlap(ray_runtime: Any) -> None:
    engine = ProgramInterpreter(spawn_ray_init_kwargs={"num_cpus": 2})
    delay = 0.2

    @do
    def worker() -> EffectGenerator[tuple[float, float]]:
        start = yield IO(time.perf_counter)
        yield IO(lambda: time.sleep(delay))
        end = yield IO(time.perf_counter)
        return start, end

    @do
    def program() -> EffectGenerator[list[tuple[float, float]]]:
        first = yield Spawn(worker(), preferred_backend="ray", num_cpus=1)
        second = yield Spawn(worker(), preferred_backend="ray", num_cpus=1)
        return (yield Gather(first.join(), second.join()))

    result = await engine.run_async(program())
    assert result.is_ok
    starts = [entry[0] for entry in result.value]
    ends = [entry[1] for entry in result.value]
    assert max(starts) < min(ends)
