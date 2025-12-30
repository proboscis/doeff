"""
Interpreter tests for Spawn effect.

Tests spawn/join with state merging, parameterized to run against
both CESK interpreter and ProgramInterpreter.
"""

from typing import TYPE_CHECKING

import pytest

from doeff import (
    EffectGenerator,
    Fail,
    Gather,
    Get,
    Log,
    Put,
    Recover,
    Spawn,
    do,
)

if TYPE_CHECKING:
    from tests.conftest import Interpreter


@pytest.mark.asyncio
async def test_spawn_join_basic(interpreter: "Interpreter"):
    """Basic spawn and join returns correct value."""

    @do
    def worker() -> EffectGenerator[int]:
        yield Put("status", "done")
        return 10

    @do
    def program() -> EffectGenerator[tuple[int, str | None]]:
        task = yield Spawn(worker())
        result = yield task.join()
        status = yield Get("status")
        return result, status

    result = await interpreter.run_async(program())

    assert result.is_ok
    # With state merging, parent should see worker's state
    assert result.value == (10, "done")


@pytest.mark.asyncio
async def test_spawn_multiple_tasks(interpreter: "Interpreter"):
    """Multiple spawn tasks run and merge state correctly."""

    @do
    def worker(index: int) -> EffectGenerator[int]:
        yield Put(f"state_{index}", index)
        return index

    @do
    def program() -> EffectGenerator[tuple[list[int], list[int]]]:
        tasks = []
        for i in range(3):
            tasks.append((yield Spawn(worker(i))))

        results = []
        for task in tasks:
            results.append((yield task.join()))

        states = []
        for i in range(3):
            states.append((yield Get(f"state_{i}")))

        return results, states

    result = await interpreter.run_async(program())

    assert result.is_ok
    # With state merging, all worker states should be visible
    assert result.value == ([0, 1, 2], [0, 1, 2])


@pytest.mark.asyncio
async def test_spawn_with_gather(interpreter: "Interpreter"):
    """Spawn tasks can be gathered."""

    @do
    def worker(index: int) -> EffectGenerator[int]:
        return index * 2

    @do
    def program() -> EffectGenerator[list[int]]:
        tasks = []
        for i in range(4):
            tasks.append((yield Spawn(worker(i))))
        return (yield Gather(*(task.join() for task in tasks)))

    result = await interpreter.run_async(program())

    assert result.is_ok
    assert result.value == [0, 2, 4, 6]


@pytest.mark.asyncio
async def test_spawn_worker_gather_programs(interpreter: "Interpreter"):
    """Spawned worker can use Gather internally."""

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
        task = yield Spawn(worker())
        total = yield task.join()
        first = yield Get("sub_1")
        second = yield Get("sub_2")
        return total, first, second

    result = await interpreter.run_async(program())

    assert result.is_ok
    # With state merging, subtask states should be visible
    assert result.value == (3, 1, 2)


@pytest.mark.asyncio
async def test_spawn_recover_handles_failure(interpreter: "Interpreter"):
    """Failed spawn can be recovered."""

    @do
    def worker() -> EffectGenerator[int]:
        yield Fail(RuntimeError("boom"))
        return 0

    @do
    def program() -> EffectGenerator[int]:
        task = yield Spawn(worker())
        return (yield Recover(task.join(), fallback=42))

    result = await interpreter.run_async(program())

    assert result.is_ok
    assert result.value == 42


@pytest.mark.asyncio
async def test_spawn_state_snapshot_read(interpreter: "Interpreter"):
    """Spawned worker reads state at spawn time."""

    @do
    def worker() -> EffectGenerator[str | None]:
        return (yield Get("flag"))

    @do
    def program() -> EffectGenerator[tuple[str | None, str | None]]:
        yield Put("flag", "before")
        task = yield Spawn(worker())
        yield Put("flag", "after")
        seen = yield task.join()
        current = yield Get("flag")
        return seen, current

    result = await interpreter.run_async(program())

    assert result.is_ok
    # Worker sees "before" (snapshot at spawn), parent keeps "after"
    assert result.value == ("before", "after")


@pytest.mark.asyncio
async def test_spawn_state_merge_preserves_parent_updates(interpreter: "Interpreter"):
    """State merging preserves both parent and worker updates."""

    @do
    def worker() -> EffectGenerator[str]:
        yield Put("worker_key", "done")
        return "ok"

    @do
    def program() -> EffectGenerator[tuple[str, int | None, str | None]]:
        yield Put("counter", 1)
        task = yield Spawn(worker())
        yield Put("counter", 2)
        value = yield task.join()
        counter = yield Get("counter")
        worker_value = yield Get("worker_key")
        return value, counter, worker_value

    result = await interpreter.run_async(program())

    assert result.is_ok
    # Parent's counter=2 should be preserved (set after spawn)
    # Worker's worker_key should be merged
    assert result.value == ("ok", 2, "done")


@pytest.mark.asyncio
async def test_spawn_exception_propagates(interpreter: "Interpreter"):
    """Exceptions from spawned worker propagate correctly."""

    @do
    def worker() -> EffectGenerator[int]:
        yield Fail(ValueError("boom"))
        return 0

    @do
    def program() -> EffectGenerator[int]:
        task = yield Spawn(worker())
        return (yield task.join())

    result = await interpreter.run_async(program())

    assert result.is_err
    assert "boom" in str(result.error)


@pytest.mark.asyncio
async def test_spawn_join_idempotent(interpreter: "Interpreter"):
    """Joining same task multiple times returns same value."""

    @do
    def worker() -> EffectGenerator[int]:
        yield Log("joined-once")
        yield Put("value", "ok")
        return 7

    @do
    def program() -> EffectGenerator[tuple[int, int, str | None]]:
        task = yield Spawn(worker())
        first = yield task.join()
        second = yield task.join()
        value = yield Get("value")
        return first, second, value

    result = await interpreter.run_async(program())

    assert result.is_ok
    assert result.value == (7, 7, "ok")
    assert result.log.count("joined-once") == 1


@pytest.mark.asyncio
async def test_spawn_nested_spawn(interpreter: "Interpreter"):
    """Nested spawn works correctly."""

    @do
    def inner() -> EffectGenerator[int]:
        return 3

    @do
    def outer() -> EffectGenerator[int]:
        inner_task = yield Spawn(inner())
        inner_value = yield inner_task.join()
        return inner_value + 1

    @do
    def program() -> EffectGenerator[int]:
        task = yield Spawn(outer())
        return (yield task.join())

    result = await interpreter.run_async(program())

    assert result.is_ok
    assert result.value == 4


@pytest.mark.asyncio
async def test_spawn_state_no_merge_on_error(interpreter: "Interpreter"):
    """On worker error, state is NOT merged (all-or-nothing)."""

    @do
    def worker() -> EffectGenerator[int]:
        yield Put("worker_state", "set")
        yield Fail(RuntimeError("boom"))
        return 0

    @do
    def program() -> EffectGenerator[str | None]:
        task = yield Spawn(worker())
        try:
            yield task.join()
        except Exception:
            pass
        return (yield Get("worker_state"))

    result = await interpreter.run_async(program())

    assert result.is_ok
    # Worker state should NOT be visible due to error (no merge)
    assert result.value is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
