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


class TestSpawnPromiseWaitCombinations:
    """Tests for Spawn + Promise + Wait combinations."""

    def test_spawned_task_creates_promise_main_waits(self) -> None:
        """Spawned task creates Promise, main task Waits on it."""
        runtime = SyncRuntime()

        @do
        def program():
            @do
            def producer():
                promise = yield CreatePromise()
                yield Await(asyncio.sleep(0.01))
                yield CompletePromise(promise, "from_producer")
                return promise

            task = yield Spawn(producer())
            promise = yield Wait(task)
            result = yield Wait(promise.future)
            return result

        result = runtime.run_and_unwrap(program())
        assert result == "from_producer"

    def test_two_spawned_tasks_producer_consumer_pattern(self) -> None:
        """Producer spawned task creates+completes Promise, consumer spawned task Waits."""
        runtime = SyncRuntime()

        @do
        def program():
            promise = yield CreatePromise()

            @do
            def producer():
                yield Await(asyncio.sleep(0.02))
                yield CompletePromise(promise, "produced_value")
                return "producer_done"

            @do
            def consumer():
                value = yield Wait(promise.future)
                return f"consumed_{value}"

            producer_task = yield Spawn(producer())
            consumer_task = yield Spawn(consumer())

            consumer_result = yield Wait(consumer_task)
            producer_result = yield Wait(producer_task)

            return {
                "producer": producer_result,
                "consumer": consumer_result,
            }

        result = runtime.run_and_unwrap(program())
        assert result["producer"] == "producer_done"
        assert result["consumer"] == "consumed_produced_value"

    def test_spawned_task_fails_promise_another_spawned_waits(self) -> None:
        """Spawned task FailPromise, another spawned task catches via Safe+Wait."""
        runtime = SyncRuntime()

        @do
        def program():
            promise = yield CreatePromise()

            @do
            def failer():
                yield Await(asyncio.sleep(0.01))
                yield FailPromise(promise, ValueError("intentional_failure"))
                return "failer_done"

            @do
            def waiter():
                safe_result = yield Safe(Wait(promise.future))
                if safe_result.is_err():
                    return f"caught_{safe_result.err()}"
                return f"got_{safe_result.ok()}"

            failer_task = yield Spawn(failer())
            waiter_task = yield Spawn(waiter())

            waiter_result = yield Wait(waiter_task)
            failer_result = yield Wait(failer_task)

            return {
                "failer": failer_result,
                "waiter": waiter_result,
            }

        result = runtime.run_and_unwrap(program())
        assert result["failer"] == "failer_done"
        assert "caught_" in result["waiter"]
        assert "intentional_failure" in result["waiter"]

    def test_multiple_spawned_waiters_on_same_promise(self) -> None:
        """Multiple spawned tasks Wait on same Promise, all receive the value."""
        runtime = SyncRuntime()

        @do
        def program():
            promise = yield CreatePromise()

            @do
            def completer():
                yield Await(asyncio.sleep(0.02))
                yield CompletePromise(promise, "shared_value")

            @do
            def waiter(waiter_id: int):
                value = yield Wait(promise.future)
                return f"waiter_{waiter_id}_got_{value}"

            completer_task = yield Spawn(completer())
            waiter1 = yield Spawn(waiter(1))
            waiter2 = yield Spawn(waiter(2))
            waiter3 = yield Spawn(waiter(3))

            r1 = yield Wait(waiter1)
            r2 = yield Wait(waiter2)
            r3 = yield Wait(waiter3)
            yield Wait(completer_task)

            return [r1, r2, r3]

        result = runtime.run_and_unwrap(program())
        assert result[0] == "waiter_1_got_shared_value"
        assert result[1] == "waiter_2_got_shared_value"
        assert result[2] == "waiter_3_got_shared_value"

    def test_spawned_task_await_then_complete_promise_then_main_wait(self) -> None:
        """Spawned task: Await -> state change -> CompletePromise, main Waits."""
        runtime = SyncRuntime()

        @do
        def program():
            promise = yield CreatePromise()

            @do
            def worker():
                yield Put("step", "started")
                yield Await(asyncio.sleep(0.01))
                yield Put("step", "after_await")
                step = yield Get("step")
                yield CompletePromise(promise, f"completed_at_{step}")
                return "worker_done"

            task = yield Spawn(worker())
            promise_result = yield Wait(promise.future)
            task_result = yield Wait(task)

            return {
                "promise": promise_result,
                "task": task_result,
            }

        result = runtime.run_and_unwrap(program())
        assert result["promise"] == "completed_at_after_await"
        assert result["task"] == "worker_done"

    def test_chain_of_spawned_tasks_with_promises(self) -> None:
        """Chain: Task A creates Promise1, Task B waits Promise1 + creates Promise2, Task C waits Promise2."""
        runtime = SyncRuntime()

        @do
        def program():
            promise1 = yield CreatePromise()
            promise2 = yield CreatePromise()

            @do
            def task_a():
                yield Await(asyncio.sleep(0.01))
                yield CompletePromise(promise1, "value_from_a")
                return "a_done"

            @do
            def task_b():
                val = yield Wait(promise1.future)
                yield Await(asyncio.sleep(0.01))
                yield CompletePromise(promise2, f"b_processed_{val}")
                return "b_done"

            @do
            def task_c():
                val = yield Wait(promise2.future)
                return f"c_got_{val}"

            ta = yield Spawn(task_a())
            tb = yield Spawn(task_b())
            tc = yield Spawn(task_c())

            rc = yield Wait(tc)
            rb = yield Wait(tb)
            ra = yield Wait(ta)

            return {"a": ra, "b": rb, "c": rc}

        result = runtime.run_and_unwrap(program())
        assert result["a"] == "a_done"
        assert result["b"] == "b_done"
        assert result["c"] == "c_got_b_processed_value_from_a"

    def test_spawned_task_fail_promise_multiple_waiters_all_fail(self) -> None:
        """FailPromise propagates to all spawned Waiters."""
        runtime = SyncRuntime()

        @do
        def program():
            promise = yield CreatePromise()

            @do
            def failer():
                yield Await(asyncio.sleep(0.02))
                yield FailPromise(promise, RuntimeError("broadcast_error"))

            @do
            def waiter(waiter_id: int):
                safe = yield Safe(Wait(promise.future))
                if safe.is_err():
                    return f"waiter_{waiter_id}_failed"
                return f"waiter_{waiter_id}_ok"

            failer_task = yield Spawn(failer())
            w1 = yield Spawn(waiter(1))
            w2 = yield Spawn(waiter(2))

            r1 = yield Wait(w1)
            r2 = yield Wait(w2)
            yield Wait(failer_task)

            return [r1, r2]

        result = runtime.run_and_unwrap(program())
        assert result[0] == "waiter_1_failed"
        assert result[1] == "waiter_2_failed"


class TestSyncRuntimePromiseWithAwait:
    """Tests for Promise effects inside spawned tasks with Await/Delay."""

    def test_complete_promise_after_await_in_spawned_task(self) -> None:
        """CompletePromise works correctly after Await in spawned task."""
        runtime = SyncRuntime()

        @do
        def program():
            promise = yield CreatePromise()

            @do
            def completer():
                yield Await(asyncio.sleep(0.01))
                yield CompletePromise(promise, "completed_after_await")

            task = yield Spawn(completer())
            result = yield Wait(promise.future)
            yield Wait(task)
            return result

        result = runtime.run_and_unwrap(program())
        assert result == "completed_after_await"

    def test_fail_promise_after_await_in_spawned_task(self) -> None:
        """FailPromise works correctly after Await in spawned task."""
        runtime = SyncRuntime()

        @do
        def program():
            promise = yield CreatePromise()

            @do
            def failer():
                yield Await(asyncio.sleep(0.01))
                yield FailPromise(promise, ValueError("failed_after_await"))

            task = yield Spawn(failer())
            safe_result = yield Safe(Wait(promise.future))
            yield Wait(task)
            return safe_result

        result = runtime.run_and_unwrap(program())
        assert result.is_err()
        assert "failed_after_await" in str(result.err())

    def test_complete_promise_after_delay_in_spawned_task(self) -> None:
        """CompletePromise works correctly after Delay in spawned task."""
        runtime = SyncRuntime()

        @do
        def program():
            promise = yield CreatePromise()

            @do
            def completer():
                yield Delay(0.01)
                yield CompletePromise(promise, "completed_after_delay")

            task = yield Spawn(completer())
            result = yield Wait(promise.future)
            yield Wait(task)
            return result

        result = runtime.run_and_unwrap(program())
        assert result == "completed_after_delay"

    def test_fail_promise_after_delay_in_spawned_task(self) -> None:
        """FailPromise works correctly after Delay in spawned task."""
        runtime = SyncRuntime()

        @do
        def program():
            promise = yield CreatePromise()

            @do
            def failer():
                yield Delay(0.01)
                yield FailPromise(promise, RuntimeError("failed_after_delay"))

            task = yield Spawn(failer())
            safe_result = yield Safe(Wait(promise.future))
            yield Wait(task)
            return safe_result

        result = runtime.run_and_unwrap(program())
        assert result.is_err()
        assert "failed_after_delay" in str(result.err())

    def test_promise_with_multiple_awaits_before_complete(self) -> None:
        """Promise completion after multiple Awaits in spawned task."""
        runtime = SyncRuntime()

        @do
        def program():
            promise = yield CreatePromise()

            @do
            def multi_await_completer():
                yield Await(asyncio.sleep(0.005))
                yield Put("step", 1)
                yield Await(asyncio.sleep(0.005))
                yield Put("step", 2)
                step = yield Get("step")
                yield CompletePromise(promise, f"done_at_step_{step}")

            task = yield Spawn(multi_await_completer())
            result = yield Wait(promise.future)
            yield Wait(task)
            return result

        result = runtime.run_and_unwrap(program())
        assert result == "done_at_step_2"

    def test_nested_spawn_with_promise_and_await(self) -> None:
        """Nested spawn where inner task completes promise after Await."""
        runtime = SyncRuntime()

        @do
        def program():
            promise = yield CreatePromise()

            @do
            def inner_completer():
                yield Await(asyncio.sleep(0.01))
                yield CompletePromise(promise, "from_inner")

            @do
            def outer_task():
                inner = yield Spawn(inner_completer())
                yield Wait(inner)
                return "outer_done"

            outer = yield Spawn(outer_task())
            promise_result = yield Wait(promise.future)
            outer_result = yield Wait(outer)
            return {"promise": promise_result, "outer": outer_result}

        result = runtime.run_and_unwrap(program())
        assert result["promise"] == "from_inner"
        assert result["outer"] == "outer_done"

    def test_fail_promise_propagates_through_wait(self) -> None:
        """FailPromise error propagates correctly through Wait."""
        runtime = SyncRuntime()

        class CustomError(Exception):
            pass

        @do
        def program():
            promise = yield CreatePromise()

            @do
            def failer():
                yield Await(asyncio.sleep(0.01))
                yield FailPromise(promise, CustomError("custom_error"))

            task = yield Spawn(failer())
            safe_result = yield Safe(Wait(promise.future))
            yield Wait(task)
            return safe_result

        result = runtime.run_and_unwrap(program())
        assert result.is_err()
        assert isinstance(result.err(), CustomError)
        assert "custom_error" in str(result.err())


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


class TestSyncRuntimeStoreSemantics:
    """Tests for correct store handling during external suspension.

    These tests verify the fix for using current store at resume time
    rather than snapshotting store at suspension time.
    """

    def test_await_preserves_state_modifications(self) -> None:
        """State modifications before Await are visible after resume."""
        runtime = SyncRuntime()

        @do
        def program():
            yield Put("counter", 0)
            yield Put("counter", 1)
            yield Await(asyncio.sleep(0.01))
            value = yield Get("counter")
            return value

        result = runtime.run_and_unwrap(program())
        assert result == 1

    def test_multiple_awaits_preserve_state_sequence(self) -> None:
        """Sequential Awaits correctly preserve all intermediate state changes."""
        runtime = SyncRuntime()

        @do
        def program():
            yield Put("step", 0)

            yield Put("step", 1)
            yield Await(asyncio.sleep(0.01))
            v1 = yield Get("step")

            yield Put("step", 2)
            yield Await(asyncio.sleep(0.01))
            v2 = yield Get("step")

            yield Put("step", 3)
            yield Await(asyncio.sleep(0.01))
            v3 = yield Get("step")

            return (v1, v2, v3)

        result = runtime.run_and_unwrap(program())
        assert result == (1, 2, 3)

    def test_spawned_task_await_uses_isolated_store(self) -> None:
        """Spawned task Await uses isolated store, not shared store."""
        runtime = SyncRuntime()

        @do
        def background():
            yield Put("bg_local", "from_bg")
            yield Await(asyncio.sleep(0.01))
            bg_value = yield Get("bg_local")
            return bg_value

        @do
        def program():
            yield Put("main_key", "main_value")
            task = yield Spawn(background())
            result = yield Wait(task)
            main_value = yield Get("main_key")
            return (result, main_value)

        result = runtime.run_and_unwrap(program())
        assert result == ("from_bg", "main_value")

    def test_delay_preserves_state(self) -> None:
        """State is preserved across Delay effects."""
        runtime = SyncRuntime()

        @do
        def program():
            yield Put("before_delay", True)
            yield Delay(0.01)
            before = yield Get("before_delay")
            yield Put("after_delay", True)
            yield Delay(0.01)
            after = yield Get("after_delay")
            return (before, after)

        result = runtime.run_and_unwrap(program())
        assert result == (True, True)

    def test_gather_with_state_modifications(self) -> None:
        """Gather correctly handles tasks that modify state and await."""
        runtime = SyncRuntime()

        @do
        def task_with_await(task_id: int):
            yield Put(f"task_{task_id}_started", True)
            yield Await(asyncio.sleep(0.01))
            yield Put(f"task_{task_id}_finished", True)
            return task_id

        @do
        def program():
            t1 = yield Spawn(task_with_await(1))
            t2 = yield Spawn(task_with_await(2))
            results = yield Gather(t1, t2)
            return results

        result = runtime.run_and_unwrap(program())
        assert result == [1, 2]

    def test_spawned_task_await_does_not_leak_store_to_main(self) -> None:
        """Spawned task's store modifications during Await don't leak to main store.
        
        This tests the fix for: "can even overwrite the global store with a
        spawned task's isolated store" (Oracle review).
        """
        runtime = SyncRuntime()

        @do
        def background():
            yield Put("leaked_key", "SHOULD_NOT_APPEAR_IN_MAIN")
            yield Await(asyncio.sleep(0.01))
            yield Put("another_leaked", "ALSO_SHOULD_NOT_LEAK")
            return "bg_done"

        @do
        def program():
            yield Put("main_key", "main_value")
            task = yield Spawn(background())
            result = yield Wait(task)
            main_value = yield Get("main_key")
            leaked = yield Safe(Get("leaked_key"))
            another = yield Safe(Get("another_leaked"))
            return {
                "bg_result": result,
                "main_key": main_value,
                "leaked_found": leaked.is_ok(),
                "another_found": another.is_ok(),
            }

        result = runtime.run_and_unwrap(program())
        assert result["bg_result"] == "bg_done"
        assert result["main_key"] == "main_value"
        assert result["leaked_found"] is False, "Spawned task store leaked to main!"
        assert result["another_found"] is False, "Spawned task store leaked to main!"

    def test_main_task_await_sees_pre_await_state(self) -> None:
        """Main task's state before Await is visible after resume.
        
        Verifies current store (not snapshot) is used at resume time.
        """
        runtime = SyncRuntime()

        @do
        def program():
            yield Put("step", "initial")
            yield Put("step", "before_await")
            yield Put("counter", 0)
            
            yield Put("counter", 1)
            yield Await(asyncio.sleep(0.01))
            
            step_after = yield Get("step")
            counter_after = yield Get("counter")
            
            return {"step": step_after, "counter": counter_after}

        result = runtime.run_and_unwrap(program())
        assert result["step"] == "before_await"
        assert result["counter"] == 1

    def test_concurrent_spawned_tasks_with_await_maintain_isolation(self) -> None:
        """Multiple spawned tasks with Await maintain store isolation.
        
        Each spawned task should see its own snapshot, not other tasks' changes.
        """
        runtime = SyncRuntime()

        @do
        def task_a():
            yield Put("shared_key", "from_a")
            yield Await(asyncio.sleep(0.02))
            value = yield Get("shared_key")
            return f"a_saw_{value}"

        @do
        def task_b():
            yield Put("shared_key", "from_b")
            yield Await(asyncio.sleep(0.01))
            value = yield Get("shared_key")
            return f"b_saw_{value}"

        @do
        def program():
            yield Put("shared_key", "main_initial")
            t1 = yield Spawn(task_a())
            t2 = yield Spawn(task_b())
            results = yield Gather(t1, t2)
            main_value = yield Get("shared_key")
            return {"results": results, "main_value": main_value}

        result = runtime.run_and_unwrap(program())
        assert result["results"][0] == "a_saw_from_a"
        assert result["results"][1] == "b_saw_from_b"
        assert result["main_value"] == "main_initial"


class TestSuspensionHandleThreadSafety:
    """Tests for SuspensionHandle thread safety."""

    def test_suspension_handle_double_complete_raises(self) -> None:
        """Double completion of SuspensionHandle raises error."""
        from doeff.cesk.runtime.context import SuspensionHandle

        results: list[str] = []

        def on_complete(v: int) -> None:
            results.append(f"complete:{v}")

        def on_fail(e: BaseException) -> None:
            results.append(f"fail:{e}")

        handle: SuspensionHandle[int] = SuspensionHandle(on_complete, on_fail)

        handle.complete(42)
        assert results == ["complete:42"]

        try:
            handle.complete(99)
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "already completed" in str(e)

    def test_suspension_handle_complete_then_fail_raises(self) -> None:
        """Fail after complete raises error."""
        from doeff.cesk.runtime.context import SuspensionHandle

        results: list[str] = []
        handle: SuspensionHandle[int] = SuspensionHandle(
            lambda v: results.append(f"complete:{v}"),
            lambda e: results.append(f"fail:{e}"),
        )

        handle.complete(42)

        try:
            handle.fail(ValueError("too late"))
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "already completed" in str(e)

    def test_suspension_handle_concurrent_complete_one_wins(self) -> None:
        """Concurrent completion attempts - exactly one succeeds.
        
        Tests the thread safety fix for SuspensionHandle.
        """
        import threading
        from doeff.cesk.runtime.context import SuspensionHandle

        results: list[int] = []
        errors: list[str] = []
        lock = threading.Lock()

        def on_complete(v: int) -> None:
            with lock:
                results.append(v)

        def on_fail(e: BaseException) -> None:
            pass

        handle: SuspensionHandle[int] = SuspensionHandle(on_complete, on_fail)
        barrier = threading.Barrier(10)

        def try_complete(value: int) -> None:
            barrier.wait()
            try:
                handle.complete(value)
            except RuntimeError as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=try_complete, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 1, f"Expected exactly 1 completion, got {len(results)}"
        assert len(errors) == 9, f"Expected 9 errors, got {len(errors)}"
        assert all("already completed" in e for e in errors)
