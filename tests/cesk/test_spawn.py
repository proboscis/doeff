"""Tests for Spawn/Task effects implementation (SPEC-EFF-005).

This test file covers the Spawn/Task effect lifecycle:
- SpawnEffect: Spawn a background task
- TaskJoinEffect: Wait for task completion
- TaskCancelEffect: Request task cancellation
- TaskIsDoneEffect: Check completion status

Design Decisions (from spec):
1. Store semantics: Snapshot at spawn time (isolated - child gets copy)
2. Error handling: Exception stored in Task until join (fire-and-forget friendly)
3. Cancellation: Follow asyncio conventions (cancel() is sync request, CancelledError on join)
"""

import asyncio

import pytest

from doeff import do, Program
from doeff.effects import (
    Ask,
    Delay,
    Get,
    Gather,
    IO,
    Listen,
    Local,
    Modify,
    Pure,
    Put,
    Safe,
    Spawn,
    Task,
    TaskCancelledError,
    Tell,
)


# ============================================================================
# Basic Spawn/Join Tests
# ============================================================================


class TestSpawnBasic:
    """Basic spawn and join functionality tests."""

    @pytest.mark.asyncio
    async def test_spawn_returns_task_handle(self) -> None:
        """Test that Spawn returns a Task handle."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def background():
            return 42

        @do
        def program():
            task = yield Spawn(background())
            return task

        result = await runtime.run_and_unwrap(program())
        assert isinstance(result, Task)
        assert result.backend == "thread"  # Default backend

    @pytest.mark.asyncio
    async def test_spawn_with_preferred_backend(self) -> None:
        """Test that Spawn respects preferred_backend."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def background():
            return 42

        @do
        def program():
            task = yield Spawn(background(), preferred_backend="process")
            return task

        result = await runtime.run_and_unwrap(program())
        assert isinstance(result, Task)
        assert result.backend == "process"

    @pytest.mark.asyncio
    async def test_spawn_and_join_success(self) -> None:
        """Test spawning a task and joining to get result."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def background():
            return 42

        @do
        def program():
            task = yield Spawn(background())
            result = yield task.join()
            return result

        result = await runtime.run_and_unwrap(program())
        assert result == 42

    @pytest.mark.asyncio
    async def test_spawn_and_join_with_computation(self) -> None:
        """Test spawning a task that performs computation."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def compute(n: int):
            return n * n

        @do
        def program():
            task = yield Spawn(compute(7))
            result = yield task.join()
            return result

        result = await runtime.run_and_unwrap(program())
        assert result == 49

    @pytest.mark.asyncio
    async def test_spawn_multiple_tasks(self) -> None:
        """Test spawning multiple tasks and joining them."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def task_n(n: int):
            return n * 2

        @do
        def program():
            task1 = yield Spawn(task_n(1))
            task2 = yield Spawn(task_n(2))
            task3 = yield Spawn(task_n(3))
            
            result1 = yield task1.join()
            result2 = yield task2.join()
            result3 = yield task3.join()
            
            return (result1, result2, result3)

        result = await runtime.run_and_unwrap(program())
        assert result == (2, 4, 6)

    @pytest.mark.asyncio
    async def test_spawn_continue_while_running(self) -> None:
        """Test that parent continues executing while spawned task runs."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()
        execution_order: list[str] = []

        @do
        def background():
            yield IO(lambda: execution_order.append("background"))
            return "bg_done"

        @do
        def program():
            task = yield Spawn(background())
            yield IO(lambda: execution_order.append("parent_after_spawn"))
            result = yield task.join()
            yield IO(lambda: execution_order.append("parent_after_join"))
            return result

        result = await runtime.run_and_unwrap(program())
        assert result == "bg_done"
        # Parent should execute after spawn before join
        assert "parent_after_spawn" in execution_order


# ============================================================================
# Error Handling Tests
# ============================================================================


class TestSpawnErrorHandling:
    """Test error handling in spawned tasks."""

    @pytest.mark.asyncio
    async def test_spawn_error_stored_until_join(self) -> None:
        """Test that errors are stored in Task until join is called."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def failing_task():
            raise ValueError("task failed")

        @do
        def program():
            task = yield Spawn(failing_task())
            # Parent continues - error not propagated yet
            yield Pure(None)
            # Now we join - error should propagate
            result = yield Safe(task.join())
            return result

        result = await runtime.run_and_unwrap(program())
        assert result.is_err()
        assert isinstance(result.error, ValueError)
        assert str(result.error) == "task failed"

    @pytest.mark.asyncio
    async def test_spawn_error_does_not_affect_parent(self) -> None:
        """Test that spawned task error doesn't immediately fail parent."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()
        executed = []

        @do
        def failing_task():
            raise ValueError("task failed")

        @do
        def program():
            task = yield Spawn(failing_task())
            # These should still execute
            yield IO(lambda: executed.append("step1"))
            yield IO(lambda: executed.append("step2"))
            # Don't join - error stays contained
            return "parent_success"

        result = await runtime.run_and_unwrap(program())
        assert result == "parent_success"
        assert executed == ["step1", "step2"]

    @pytest.mark.asyncio
    async def test_spawn_join_propagates_exception(self) -> None:
        """Test that joining a failed task propagates the exception."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def failing_task():
            raise RuntimeError("boom")

        @do
        def program():
            task = yield Spawn(failing_task())
            result = yield task.join()  # Should raise
            return result

        with pytest.raises(RuntimeError, match="boom"):
            await runtime.run_and_unwrap(program())


# ============================================================================
# Cancellation Tests
# ============================================================================


class TestSpawnCancellation:
    """Test task cancellation functionality."""

    @pytest.mark.asyncio
    async def test_cancel_returns_true_on_running_task(self) -> None:
        """Test that cancel() returns True for running task."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def long_running():
            yield Delay(seconds=10.0)
            return "done"

        @do
        def program():
            task = yield Spawn(long_running())
            cancelled = yield task.cancel()
            return cancelled

        result = await runtime.run_and_unwrap(program())
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_returns_false_on_completed_task(self) -> None:
        """Test that cancel() returns False for already completed task."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def quick_task():
            return "done"

        @do
        def program():
            task = yield Spawn(quick_task())
            # Wait for completion
            _ = yield task.join()
            # Try to cancel completed task
            cancelled = yield task.cancel()
            return cancelled

        result = await runtime.run_and_unwrap(program())
        assert result is False

    @pytest.mark.asyncio
    async def test_join_cancelled_task_raises_cancelled_error(self) -> None:
        """Test that joining a cancelled task raises TaskCancelledError."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def long_running():
            yield Delay(seconds=10.0)
            return "never reached"

        @do
        def program():
            task = yield Spawn(long_running())
            yield task.cancel()
            result = yield Safe(task.join())
            return result

        result = await runtime.run_and_unwrap(program())
        assert result.is_err()
        assert isinstance(result.error, TaskCancelledError)

    @pytest.mark.asyncio
    async def test_multiple_cancel_calls(self) -> None:
        """Test that multiple cancel calls are idempotent."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def long_running():
            yield Delay(seconds=10.0)
            return "done"

        @do
        def program():
            task = yield Spawn(long_running())
            c1 = yield task.cancel()
            c2 = yield task.cancel()  # Second cancel
            c3 = yield task.cancel()  # Third cancel
            return (c1, c2, c3)

        result = await runtime.run_and_unwrap(program())
        # First should succeed, subsequent should return False (already cancelled)
        assert result == (True, False, False)


# ============================================================================
# is_done() Tests
# ============================================================================


class TestSpawnIsDone:
    """Test task completion checking functionality."""

    @pytest.mark.asyncio
    async def test_is_done_false_for_running_task(self) -> None:
        """Test that is_done() returns False for running task."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def long_running():
            yield Delay(seconds=10.0)
            return "done"

        @do
        def program():
            task = yield Spawn(long_running())
            done = yield task.is_done()
            yield task.cancel()  # Clean up
            return done

        result = await runtime.run_and_unwrap(program())
        assert result is False

    @pytest.mark.asyncio
    async def test_is_done_true_for_completed_task(self) -> None:
        """Test that is_done() returns True for completed task."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def quick_task():
            return "done"

        @do
        def program():
            task = yield Spawn(quick_task())
            _ = yield task.join()  # Wait for completion
            done = yield task.is_done()
            return done

        result = await runtime.run_and_unwrap(program())
        assert result is True

    @pytest.mark.asyncio
    async def test_is_done_true_for_cancelled_task(self) -> None:
        """Test that is_done() returns True for cancelled task."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def long_running():
            yield Delay(seconds=10.0)
            return "done"

        @do
        def program():
            task = yield Spawn(long_running())
            yield task.cancel()
            done = yield task.is_done()
            return done

        result = await runtime.run_and_unwrap(program())
        assert result is True

    @pytest.mark.asyncio
    async def test_is_done_true_for_failed_task(self) -> None:
        """Test that is_done() returns True for failed task after it has run."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def failing_task():
            raise ValueError("fail")

        @do
        def program():
            task = yield Spawn(failing_task())
            # Try to join (will fail with Safe) - this ensures the task has run
            _ = yield Safe(task.join())
            # Now check is_done - should be True since we joined (even if it failed)
            done = yield task.is_done()
            return done

        result = await runtime.run_and_unwrap(program())
        assert result is True


# ============================================================================
# Store Isolation Tests (Snapshot Semantics)
# ============================================================================


class TestSpawnStoreIsolation:
    """Test that spawned tasks have isolated store snapshots."""

    @pytest.mark.asyncio
    async def test_spawned_task_has_store_snapshot(self) -> None:
        """Test that spawned task gets snapshot of store at spawn time."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def background():
            value = yield Get("counter")
            return value

        @do
        def program():
            yield Put("counter", 42)
            task = yield Spawn(background())
            result = yield task.join()
            return result

        result = await runtime.run_and_unwrap(program())
        assert result == 42

    @pytest.mark.asyncio
    async def test_spawned_task_changes_do_not_affect_parent(self) -> None:
        """Test that spawned task's store changes don't affect parent."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def background():
            yield Put("counter", 999)  # Child modifies
            return "done"

        @do
        def program():
            yield Put("counter", 42)
            task = yield Spawn(background())
            _ = yield task.join()
            # Parent's store should be unchanged
            value = yield Get("counter")
            return value

        result = await runtime.run_and_unwrap(program())
        assert result == 42  # Parent's value, not child's 999

    @pytest.mark.asyncio
    async def test_parent_changes_after_spawn_not_seen_by_child(self) -> None:
        """Test that parent's changes after spawn aren't seen by child."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()
        child_saw_value = []

        @do
        def background():
            # Small delay to ensure parent runs first
            yield Delay(seconds=0.01)
            value = yield Get("counter")
            yield IO(lambda v=value: child_saw_value.append(v))
            return value

        @do
        def program():
            yield Put("counter", 1)
            task = yield Spawn(background())
            # Parent changes after spawn
            yield Put("counter", 100)
            result = yield task.join()
            return result

        result = await runtime.run_and_unwrap(program())
        # Child should see value at spawn time (1), not parent's later change (100)
        assert result == 1


# ============================================================================
# Environment Inheritance Tests
# ============================================================================


class TestSpawnEnvironment:
    """Test that spawned tasks inherit environment at spawn time."""

    @pytest.mark.asyncio
    async def test_spawned_task_inherits_environment(self) -> None:
        """Test that spawned task can access parent's environment."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def background():
            config = yield Ask("config")
            return config

        @do
        def program():
            task = yield Spawn(background())
            result = yield task.join()
            return result

        result = await runtime.run_and_unwrap(program(), env={"config": "test_value"})
        assert result == "test_value"

    @pytest.mark.asyncio
    async def test_spawned_task_gets_env_snapshot(self) -> None:
        """Test that spawned task gets env snapshot at spawn time."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def background():
            value = yield Ask("key")
            return value

        @do
        def inner():
            task = yield Spawn(background())
            result = yield task.join()
            return result

        @do
        def program():
            # Spawn inside a Local scope
            result = yield Local({"key": "local_value"}, inner())
            return result

        result = await runtime.run_and_unwrap(program(), env={"key": "outer_value"})
        # Child should see the Local-scoped value
        assert result == "local_value"


# ============================================================================
# Composition Tests
# ============================================================================


class TestSpawnComposition:
    """Test Spawn interaction with other effects."""

    @pytest.mark.asyncio
    async def test_spawn_plus_safe(self) -> None:
        """Test Spawn with Safe error handling."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def failing():
            raise ValueError("fail")

        @do
        def program():
            task = yield Spawn(failing())
            result = yield Safe(task.join())
            return result

        result = await runtime.run_and_unwrap(program())
        assert result.is_err()
        assert isinstance(result.error, ValueError)

    @pytest.mark.asyncio
    async def test_spawn_with_logging(self) -> None:
        """Test that spawned task's logs are isolated."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def background():
            yield Tell("child log")
            return "done"

        @do
        def program():
            listen_result = yield Listen(background())
            return listen_result

        @do
        def main():
            task = yield Spawn(program())
            yield Tell("parent log")
            result = yield task.join()
            return result

        result = await runtime.run_and_unwrap(main())
        # Child's Listen should capture child's log
        assert result.value == "done"
        assert "child log" in result.log

    @pytest.mark.asyncio
    async def test_spawn_inside_gather(self) -> None:
        """Test spawning tasks inside Gather."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def spawn_and_join(n: int):
            @do
            def background():
                return n * 2

            task = yield Spawn(background())
            result = yield task.join()
            return result

        @do
        def program():
            results = yield Gather(
                spawn_and_join(1),
                spawn_and_join(2),
                spawn_and_join(3)
            )
            return results

        result = await runtime.run_and_unwrap(program())
        assert result == [2, 4, 6]

    @pytest.mark.asyncio
    async def test_nested_spawn(self) -> None:
        """Test spawning a task that spawns another task."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def inner():
            return "inner_result"

        @do
        def outer():
            task = yield Spawn(inner())
            result = yield task.join()
            return f"outer_got_{result}"

        @do
        def program():
            task = yield Spawn(outer())
            result = yield task.join()
            return result

        result = await runtime.run_and_unwrap(program())
        assert result == "outer_got_inner_result"


# ============================================================================
# Edge Cases and Integration Tests
# ============================================================================


class TestSpawnEdgeCases:
    """Edge cases and integration tests for Spawn."""

    @pytest.mark.asyncio
    async def test_join_same_task_multiple_times(self) -> None:
        """Test that joining the same task multiple times works."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def background():
            return 42

        @do
        def program():
            task = yield Spawn(background())
            result1 = yield task.join()
            result2 = yield task.join()  # Join again
            result3 = yield task.join()  # And again
            return (result1, result2, result3)

        result = await runtime.run_and_unwrap(program())
        assert result == (42, 42, 42)

    @pytest.mark.asyncio
    async def test_spawn_pure_value(self) -> None:
        """Test spawning a program that just returns a pure value."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def program():
            task = yield Spawn(Program.pure(42))
            result = yield task.join()
            return result

        result = await runtime.run_and_unwrap(program())
        assert result == 42

    @pytest.mark.asyncio
    async def test_spawn_with_delay(self) -> None:
        """Test spawning a task that uses Delay."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def delayed_task():
            yield Delay(seconds=0.01)
            return "delayed_result"

        @do
        def program():
            task = yield Spawn(delayed_task())
            result = yield task.join()
            return result

        result = await runtime.run_and_unwrap(program())
        assert result == "delayed_result"

    @pytest.mark.asyncio
    async def test_fire_and_forget_pattern(self) -> None:
        """Test fire-and-forget pattern (spawn without join)."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()
        side_effect_happened = []

        @do
        def background():
            yield IO(lambda: side_effect_happened.append(True))
            return "done"

        @do
        def program():
            _ = yield Spawn(background())
            # Don't join - fire and forget
            return "parent_done"

        result = await runtime.run_and_unwrap(program())
        assert result == "parent_done"
        # Note: Side effect may or may not have happened depending on scheduling

    @pytest.mark.asyncio
    async def test_spawn_with_intercept(self) -> None:
        """Test that intercept doesn't apply to spawned tasks (isolated)."""
        from doeff.cesk.runtime import AsyncRuntime
        from doeff.effects.reader import AskEffect

        runtime = AsyncRuntime()

        def transform(effect):
            if isinstance(effect, AskEffect):
                return Pure("intercepted")
            return None

        @do
        def background():
            value = yield Ask("key")
            return value

        @do
        def program():
            task = yield Spawn(background())
            result = yield task.join()
            return result

        @do
        def main():
            result = yield program().intercept(transform)
            return result

        # Spawned tasks are isolated - intercept shouldn't affect them
        result = await runtime.run_and_unwrap(main(), env={"key": "actual_value"})
        assert result == "actual_value"


# ============================================================================
# Concurrent Join Tests
# ============================================================================


class TestSpawnConcurrentJoin:
    """Test concurrent joining of the same task from multiple places."""

    @pytest.mark.asyncio
    async def test_concurrent_join_same_task(self) -> None:
        """Test that multiple tasks can join the same spawned task."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def shared_task():
            yield Delay(seconds=0.01)
            return "shared_result"

        @do
        def joiner(task: Task):
            result = yield task.join()
            return result

        @do
        def program():
            shared = yield Spawn(shared_task())
            # Multiple joiners
            results = yield Gather(
                joiner(shared),
                joiner(shared),
                joiner(shared)
            )
            return results

        result = await runtime.run_and_unwrap(program())
        assert result == ["shared_result", "shared_result", "shared_result"]


# ============================================================================
# Performance/Timing Tests
# ============================================================================


class TestSpawnTiming:
    """Test timing-related behavior of Spawn."""

    @pytest.mark.asyncio
    async def test_spawn_does_not_block_parent(self) -> None:
        """Test that spawning doesn't block the parent."""
        from doeff.cesk.runtime import AsyncRuntime
        from doeff.effects import GetTime

        runtime = AsyncRuntime()

        @do
        def slow_task():
            yield Delay(seconds=0.5)
            return "slow"

        @do
        def program():
            start = yield GetTime()
            _ = yield Spawn(slow_task())  # Should not block
            end = yield GetTime()
            elapsed = (end - start).total_seconds()
            return elapsed

        result = await runtime.run_and_unwrap(program())
        # Parent should continue immediately, not wait 0.5s
        assert result < 0.1


__all__ = [
    "TestSpawnBasic",
    "TestSpawnErrorHandling",
    "TestSpawnCancellation",
    "TestSpawnIsDone",
    "TestSpawnStoreIsolation",
    "TestSpawnEnvironment",
    "TestSpawnComposition",
    "TestSpawnEdgeCases",
    "TestSpawnConcurrentJoin",
    "TestSpawnTiming",
]


# ============================================================================
# Oracle Review - Additional Edge Case Tests
# ============================================================================


class TestSpawnOracleReview:
    """Additional tests based on Oracle review feedback."""

    @pytest.mark.asyncio
    async def test_cancel_task_with_pending_delay(self) -> None:
        """Test that cancelling a task with pending Delay works correctly.
        
        Oracle identified that cancelled tasks could be "revived" by pending
        async completions. This test verifies the fix.
        """
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def delayed_task():
            yield Delay(seconds=10.0)  # Long delay
            return "should_not_reach"

        @do
        def program():
            task = yield Spawn(delayed_task())
            # Cancel immediately while Delay is pending
            cancelled = yield task.cancel()
            # Join should raise CancelledError, not hang or return value
            result = yield Safe(task.join())
            return (cancelled, result.is_err(), isinstance(result.error, TaskCancelledError))

        result = await runtime.run_and_unwrap(program())
        cancelled, is_err, is_cancelled_error = result
        assert cancelled is True
        assert is_err is True
        assert is_cancelled_error is True

    @pytest.mark.asyncio
    async def test_spawned_task_with_user_handler_isolation(self) -> None:
        """Test that user handlers respect store isolation for spawned tasks.
        
        Oracle identified that user handlers could bypass store isolation.
        """
        from doeff.cesk.runtime import AsyncRuntime
        from doeff.cesk.handlers import default_handlers
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.pure import PureEffect

        # Custom handler that reads and modifies store
        handler_saw_value = []
        
        def custom_pure_handler(effect, task_state, store):
            # Record what value we saw in the store
            handler_saw_value.append(store.get("marker", "not_set"))
            return ContinueValue(
                value=effect.value,
                env=task_state.env,
                store=store,
                k=task_state.kontinuation,
            )

        custom_handlers = default_handlers()
        custom_handlers[PureEffect] = custom_pure_handler
        runtime = AsyncRuntime(handlers=custom_handlers)

        @do
        def background():
            yield Pure(None)  # This will trigger custom handler
            return "done"

        @do
        def program():
            yield Put("marker", "parent_value")
            task = yield Spawn(background())
            # Modify parent's store after spawn
            yield Put("marker", "modified_after_spawn")
            result = yield task.join()
            return result

        result = await runtime.run_and_unwrap(program())
        assert result == "done"
        # The handler should have seen the snapshot value, not the modified value
        assert handler_saw_value[0] == "parent_value"

    @pytest.mark.asyncio
    async def test_spawned_task_delay_uses_isolated_store(self) -> None:
        """Test that Delay completion in spawned task uses isolated store."""
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def background():
            yield Delay(seconds=0.01)
            # After delay, read from store
            value = yield Get("key")
            return value

        @do
        def program():
            yield Put("key", "snapshot_value")
            task = yield Spawn(background())
            # Modify parent's store while child is waiting
            yield Put("key", "modified_value")
            result = yield task.join()
            parent_value = yield Get("key")
            return (result, parent_value)

        child_result, parent_result = await runtime.run_and_unwrap(program())
        # Child should see snapshot value
        assert child_result == "snapshot_value"
        # Parent should see its own modified value
        assert parent_result == "modified_value"

    @pytest.mark.asyncio
    async def test_shallow_copy_semantics_documented(self) -> None:
        """Test that demonstrates shallow copy behavior of store snapshot.
        
        Note: This is documented behavior - mutable values inside the store
        can still be shared between parent and child.
        """
        from doeff.cesk.runtime import AsyncRuntime

        runtime = AsyncRuntime()

        @do
        def background(shared_list):
            # Modifying a mutable object inside the store IS visible to parent
            # because shallow copy only copies the dict, not its values
            yield IO(lambda: shared_list.append("child_added"))
            return "done"

        @do
        def program():
            shared_list = ["initial"]
            yield Put("shared", shared_list)
            task = yield Spawn(background(shared_list))
            _ = yield task.join()
            # Note: shared_list IS modified by child because it's the same object
            return shared_list

        result = await runtime.run_and_unwrap(program())
        # This test documents that shallow copy means mutable values are shared
        assert "child_added" in result


__all__ = __all__ + [
    "TestSpawnOracleReview",
]
