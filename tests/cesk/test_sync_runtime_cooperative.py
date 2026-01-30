"""Tests for SyncRuntime cooperative scheduling and external suspension (ISSUE-CORE-459).

This test file covers:
- Cooperative multi-task scheduling
- External suspension via Await effects
- Spawn/Wait with SyncRuntime
- Gather/Race with multiple spawned tasks
"""

import asyncio

from doeff import do
from doeff.cesk.runtime import SyncRuntime
from doeff.effects import (
    Await,
    Delay,
    Gather,
    Get,
    Put,
    Safe,
    Spawn,
    Wait,
)
from doeff.effects.promise import CompletePromise, CreatePromise, FailPromise
from doeff.effects.race import Race


class TestSyncRuntimeBasic:

    def test_simple_program(self) -> None:
        runtime = SyncRuntime()

        @do
        def program():
            return 42

        result = runtime.run_and_unwrap(program())
        assert result == 42

    def test_state_effects(self) -> None:
        runtime = SyncRuntime()

        @do
        def program():
            yield Put("counter", 0)
            value = yield Get("counter")
            yield Put("counter", value + 1)
            return (yield Get("counter"))

        result = runtime.run_and_unwrap(program())
        assert result == 1


class TestSyncRuntimeSpawn:

    def test_spawn_and_wait_success(self) -> None:
        runtime = SyncRuntime()

        @do
        def background():
            return 42

        @do
        def program():
            task = yield Spawn(background())
            result = yield Wait(task)
            return result

        result = runtime.run_and_unwrap(program())
        assert result == 42

    def test_spawn_multiple_tasks(self) -> None:
        runtime = SyncRuntime()

        @do
        def task1():
            return 1

        @do
        def task2():
            return 2

        @do
        def task3():
            return 3

        @do
        def program():
            t1 = yield Spawn(task1())
            t2 = yield Spawn(task2())
            t3 = yield Spawn(task3())
            r1 = yield Wait(t1)
            r2 = yield Wait(t2)
            r3 = yield Wait(t3)
            return r1 + r2 + r3

        result = runtime.run_and_unwrap(program())
        assert result == 6

    def test_spawn_with_state_isolation(self) -> None:
        runtime = SyncRuntime()

        @do
        def background():
            yield Put("key", "child")
            return (yield Get("key"))

        @do
        def program():
            yield Put("key", "parent")
            task = yield Spawn(background())
            result = yield Wait(task)
            parent_key = yield Get("key")
            return (result, parent_key)

        result = runtime.run_and_unwrap(program())
        assert result == ("child", "parent")

    def test_spawn_error_propagation(self) -> None:
        runtime = SyncRuntime()

        @do
        def failing_task():
            raise ValueError("task failed")

        @do
        def program():
            task = yield Spawn(failing_task())
            safe_result = yield Safe(Wait(task))
            return safe_result

        result = runtime.run_and_unwrap(program())
        assert result.is_err()
        assert isinstance(result.err(), ValueError)
        assert str(result.err()) == "task failed"

    def test_spawn_cancel_task(self) -> None:
        runtime = SyncRuntime()

        @do
        def long_running():
            yield Delay(10.0)
            return "completed"

        @do
        def program():
            task = yield Spawn(long_running())
            was_cancelled = yield task.cancel()
            safe_result = yield Safe(Wait(task))
            return (was_cancelled, safe_result.is_err())

        result = runtime.run_and_unwrap(program())
        assert result[0] is True
        assert result[1] is True


class TestSyncRuntimeAwait:

    def test_await_simple_coroutine(self) -> None:
        runtime = SyncRuntime()

        async def async_work():
            await asyncio.sleep(0.01)
            return 42

        @do
        def program():
            result = yield Await(async_work())
            return result

        result = runtime.run_and_unwrap(program())
        assert result == 42

    def test_await_multiple_coroutines(self) -> None:
        runtime = SyncRuntime()

        async def async_a():
            await asyncio.sleep(0.01)
            return "a"

        async def async_b():
            await asyncio.sleep(0.01)
            return "b"

        @do
        def program():
            r1 = yield Await(async_a())
            r2 = yield Await(async_b())
            return r1 + r2

        result = runtime.run_and_unwrap(program())
        assert result == "ab"

    def test_await_with_exception(self) -> None:
        runtime = SyncRuntime()

        async def failing_async():
            await asyncio.sleep(0.01)
            raise RuntimeError("async failed")

        @do
        def program():
            safe_result = yield Safe(Await(failing_async()))
            return safe_result

        result = runtime.run_and_unwrap(program())
        assert result.is_err()
        assert isinstance(result.err(), RuntimeError)


class TestSyncRuntimeDelay:

    def test_delay_basic(self) -> None:
        import time

        runtime = SyncRuntime()

        @do
        def program():
            start = time.time()
            yield Delay(0.05)
            elapsed = time.time() - start
            return elapsed >= 0.04

        result = runtime.run_and_unwrap(program())
        assert result is True

    def test_delay_in_spawned_task(self) -> None:
        runtime = SyncRuntime()

        @do
        def delayed_task():
            yield Delay(0.01)
            return "done"

        @do
        def program():
            task = yield Spawn(delayed_task())
            return (yield Wait(task))

        result = runtime.run_and_unwrap(program())
        assert result == "done"


class TestSyncRuntimeGather:

    def test_gather_multiple_tasks(self) -> None:
        runtime = SyncRuntime()

        @do
        def task_a():
            return "a"

        @do
        def task_b():
            return "b"

        @do
        def task_c():
            return "c"

        @do
        def program():
            t1 = yield Spawn(task_a())
            t2 = yield Spawn(task_b())
            t3 = yield Spawn(task_c())
            results = yield Gather(t1, t2, t3)
            return results

        result = runtime.run_and_unwrap(program())
        assert result == ["a", "b", "c"]

    def test_gather_preserves_order(self) -> None:
        runtime = SyncRuntime()

        @do
        def task_1():
            yield Delay(0.02)
            return 1

        @do
        def task_2():
            yield Delay(0.01)
            return 2

        @do
        def program():
            t1 = yield Spawn(task_1())
            t2 = yield Spawn(task_2())
            results = yield Gather(t1, t2)
            return results

        result = runtime.run_and_unwrap(program())
        assert result == [1, 2]


class TestSyncRuntimeRace:

    def test_race_first_completes(self) -> None:
        runtime = SyncRuntime()

        @do
        def fast_task():
            return "fast"

        @do
        def slow_task():
            yield Delay(1.0)
            return "slow"

        @do
        def program():
            t1 = yield Spawn(fast_task())
            t2 = yield Spawn(slow_task())
            race_result = yield Race(t1, t2)
            return race_result.value

        result = runtime.run_and_unwrap(program())
        assert result == "fast"


class TestSyncRuntimePromise:

    def test_create_and_complete_promise(self) -> None:
        runtime = SyncRuntime()

        @do
        def program():
            promise = yield CreatePromise()

            @do
            def completer():
                yield CompletePromise(promise, 42)

            task = yield Spawn(completer())
            yield Wait(task)
            result = yield Wait(promise.future)
            return result

        result = runtime.run_and_unwrap(program())
        assert result == 42

    def test_create_and_fail_promise(self) -> None:
        runtime = SyncRuntime()

        @do
        def program():
            promise = yield CreatePromise()

            @do
            def failer():
                yield FailPromise(promise, ValueError("failed"))

            task = yield Spawn(failer())
            yield Wait(task)
            safe_result = yield Safe(Wait(promise.future))
            return safe_result

        result = runtime.run_and_unwrap(program())
        assert result.is_err()
        assert isinstance(result.err(), ValueError)


class TestSyncRuntimeCooperativeScheduling:

    def test_cooperative_interleaving(self) -> None:
        runtime = SyncRuntime()
        execution_order: list[str] = []

        @do
        def task_a():
            nonlocal execution_order
            execution_order.append("a-start")
            value = yield Get("shared")
            execution_order.append(f"a-read-{value}")
            yield Put("shared", "a")
            execution_order.append("a-end")
            return "a-done"

        @do
        def task_b():
            nonlocal execution_order
            execution_order.append("b-start")
            value = yield Get("shared")
            execution_order.append(f"b-read-{value}")
            yield Put("shared", "b")
            execution_order.append("b-end")
            return "b-done"

        @do
        def program():
            yield Put("shared", "init")
            t1 = yield Spawn(task_a())
            t2 = yield Spawn(task_b())
            r1 = yield Wait(t1)
            r2 = yield Wait(t2)
            return (r1, r2)

        result = runtime.run_and_unwrap(program())
        assert result == ("a-done", "b-done")
        assert "a-start" in execution_order
        assert "b-start" in execution_order
