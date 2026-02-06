"""Tests for SPEC-EFF-005 concurrency API: Future, Promise, Wait, Race, Gather."""

import asyncio

import pytest

from doeff import (
    Await,
    CompletePromise,
    CreatePromise,
    FailPromise,
    Future,
    Gather,
    Promise,
    Race,
    RaceResult,
    Spawn,
    Task,
    Wait,
    do,
)
from doeff.cesk.run import async_handlers_preset, async_run
from doeff.effects.spawn import TaskCancelledError, Waitable


class TestFutureProtocol:
    @pytest.mark.asyncio
    async def test_task_is_waitable(self) -> None:
        """Task from Spawn is a Waitable."""

        @do
        def program():
            task = yield Spawn(do(lambda: 42)())
            return isinstance(task, Waitable)

        result = await async_run(program(), async_handlers_preset)
        assert result.value is True

    @pytest.mark.asyncio
    async def test_future_has_handle(self) -> None:
        """Future has _handle attribute."""

        @do
        def program():
            task = yield Spawn(do(lambda: 42)())
            return hasattr(task, "_handle")

        result = await async_run(program(), async_handlers_preset)
        assert result.value is True


class TestSpawnEffect:
    @pytest.mark.asyncio
    async def test_spawn_returns_task(self) -> None:
        """Spawn returns a Task."""

        @do
        def program():
            task = yield Spawn(do(lambda: 42)())
            return isinstance(task, Task)

        result = await async_run(program(), async_handlers_preset)
        assert result.value is True

    @pytest.mark.asyncio
    async def test_spawn_runs_in_background(self) -> None:
        """Spawned task runs while parent continues."""

        @do
        def slow():
            yield Await(asyncio.sleep(0.05))
            return "done"

        @do
        def program():
            task = yield Spawn(slow())
            is_done_before = yield task.is_done()
            yield Await(asyncio.sleep(0.1))
            is_done_after = yield task.is_done()
            return (is_done_before, is_done_after)

        result = await async_run(program(), async_handlers_preset)
        assert result.value == (False, True)

    @pytest.mark.asyncio
    async def test_spawn_isolates_store(self) -> None:
        """Spawned task has isolated store."""
        from doeff import Get, Put

        @do
        def child():
            yield Put("x", 100)
            return (yield Get("x"))

        @do
        def program():
            yield Put("x", 1)
            task = yield Spawn(child())
            yield Put("x", 2)
            child_value = yield Wait(task)
            parent_value = yield Get("x")
            return (child_value, parent_value)

        result = await async_run(program(), async_handlers_preset)
        assert result.value == (100, 2)


class TestWaitEffect:
    @pytest.mark.asyncio
    async def test_wait_returns_value(self) -> None:
        """Wait returns the Future's value."""

        @do
        def child():
            return 42

        @do
        def program():
            task = yield Spawn(child())
            result = yield Wait(task)
            return result

        result = await async_run(program(), async_handlers_preset)
        assert result.value == 42

    @pytest.mark.asyncio
    async def test_wait_propagates_error(self) -> None:
        """Wait propagates errors from the Future."""

        @do
        def failing_child():
            raise ValueError("child failed")

        @do
        def program():
            task = yield Spawn(failing_child())
            result = yield Wait(task)
            return result

        result = await async_run(program(), async_handlers_preset)
        assert result.is_err
        assert "child failed" in str(result.error)

    @pytest.mark.asyncio
    async def test_wait_on_cancelled_task(self) -> None:
        """Wait on cancelled task raises TaskCancelledError."""

        @do
        def slow_child():
            yield Await(asyncio.sleep(10.0))
            return "never"

        @do
        def program():
            task = yield Spawn(slow_child())
            yield task.cancel()
            result = yield Wait(task)
            return result

        result = await async_run(program(), async_handlers_preset)
        assert result.is_err
        assert isinstance(result.error, TaskCancelledError)

    @pytest.mark.asyncio
    async def test_wait_multiple_tasks_sequentially(self) -> None:
        """Can Wait on multiple tasks one after another."""

        @do
        def make_value(x: int):
            return x * 2

        @do
        def program():
            t1 = yield Spawn(make_value(1))
            t2 = yield Spawn(make_value(2))
            t3 = yield Spawn(make_value(3))

            r1 = yield Wait(t1)
            r2 = yield Wait(t2)
            r3 = yield Wait(t3)
            return [r1, r2, r3]

        result = await async_run(program(), async_handlers_preset)
        assert result.value == [2, 4, 6]


class TestTaskCancel:
    @pytest.mark.asyncio
    async def test_cancel_returns_true(self) -> None:
        """Cancel returns True when task is running."""

        @do
        def slow():
            yield Await(asyncio.sleep(10.0))
            return "done"

        @do
        def program():
            task = yield Spawn(slow())
            cancelled = yield task.cancel()
            return cancelled

        result = await async_run(program(), async_handlers_preset)
        assert result.value is True

    @pytest.mark.asyncio
    async def test_cancel_completed_returns_false(self) -> None:
        """Cancel returns False when task already completed."""

        @do
        def fast():
            return "done"

        @do
        def program():
            task = yield Spawn(fast())
            yield Wait(task)
            cancelled = yield task.cancel()
            return cancelled

        result = await async_run(program(), async_handlers_preset)
        assert result.value is False


class TestTaskIsDone:
    @pytest.mark.asyncio
    async def test_is_done_false_while_running(self) -> None:
        """is_done returns False while task is running."""

        @do
        def slow():
            yield Await(asyncio.sleep(10.0))
            return "done"

        @do
        def program():
            task = yield Spawn(slow())
            is_done = yield task.is_done()
            yield task.cancel()
            return is_done

        result = await async_run(program(), async_handlers_preset)
        assert result.value is False

    @pytest.mark.asyncio
    async def test_is_done_true_after_completion(self) -> None:
        """is_done returns True after task completes."""

        @do
        def fast():
            return "done"

        @do
        def program():
            task = yield Spawn(fast())
            yield Wait(task)
            is_done = yield task.is_done()
            return is_done

        result = await async_run(program(), async_handlers_preset)
        assert result.value is True


class TestRaceEffect:
    @pytest.mark.asyncio
    async def test_race_returns_race_result(self) -> None:
        """Race returns RaceResult with first, value, rest."""

        @do
        def fast():
            return "fast"

        @do
        def slow():
            yield Await(asyncio.sleep(1.0))
            return "slow"

        @do
        def program():
            t1 = yield Spawn(fast())
            t2 = yield Spawn(slow())
            result = yield Race(t1, t2)
            return result

        result = await async_run(program(), async_handlers_preset)

        race_result = result.value
        assert isinstance(race_result, RaceResult)
        assert race_result.value == "fast"
        assert race_result.first is not None
        assert len(race_result.rest) == 1

    @pytest.mark.asyncio
    async def test_race_first_is_winner(self) -> None:
        """RaceResult.first is the Future that won."""

        @do
        def fast():
            return "winner"

        @do
        def slow():
            yield Await(asyncio.sleep(1.0))
            return "loser"

        @do
        def program():
            t_fast = yield Spawn(fast())
            t_slow = yield Spawn(slow())
            result = yield Race(t_fast, t_slow)
            return (result.first is t_fast, result.value)

        result = await async_run(program(), async_handlers_preset)
        assert result.value == (True, "winner")

    @pytest.mark.asyncio
    async def test_race_rest_contains_losers(self) -> None:
        """RaceResult.rest contains non-winning Futures."""

        @do
        def fast():
            return "fast"

        @do
        def slow1():
            yield Await(asyncio.sleep(1.0))
            return "slow1"

        @do
        def slow2():
            yield Await(asyncio.sleep(2.0))
            return "slow2"

        @do
        def program():
            t1 = yield Spawn(fast())
            t2 = yield Spawn(slow1())
            t3 = yield Spawn(slow2())
            result = yield Race(t1, t2, t3)

            losers = result.rest
            return (len(losers), t2 in losers, t3 in losers)

        result = await async_run(program(), async_handlers_preset)
        assert result.value == (2, True, True)

    @pytest.mark.asyncio
    async def test_race_can_cancel_losers(self) -> None:
        """Can cancel losing Futures after Race completes."""

        @do
        def fast():
            return "fast"

        @do
        def slow():
            yield Await(asyncio.sleep(10.0))
            return "slow"

        @do
        def program():
            t1 = yield Spawn(fast())
            t2 = yield Spawn(slow())
            result = yield Race(t1, t2)

            for loser in result.rest:
                yield loser.cancel()

            return result.value

        result = await async_run(program(), async_handlers_preset)
        assert result.value == "fast"

    @pytest.mark.asyncio
    async def test_race_error_propagates(self) -> None:
        """If first to complete is an error, Race propagates it."""

        @do
        def fast_fail():
            raise ValueError("fast error")

        @do
        def slow():
            yield Await(asyncio.sleep(1.0))
            return "slow"

        @do
        def program():
            t1 = yield Spawn(fast_fail())
            t2 = yield Spawn(slow())
            result = yield Race(t1, t2)
            return result

        result = await async_run(program(), async_handlers_preset)
        assert result.is_err
        assert "fast error" in str(result.error)

    def test_race_requires_futures(self) -> None:
        """Race only accepts Futures, not Programs."""

        @do
        def prog():
            return 1

        with pytest.raises(TypeError, match="must be Waitable"):
            Race(prog(), prog())

    def test_race_requires_at_least_one(self) -> None:
        """Race requires at least one Future."""
        with pytest.raises(ValueError, match="at least one"):
            Race()


class TestGatherEffect:
    @pytest.mark.asyncio
    async def test_gather_futures_returns_list(self) -> None:
        """Gather with Futures returns list of results."""

        @do
        def make_value(x: int):
            return x

        @do
        def program():
            t1 = yield Spawn(make_value(1))
            t2 = yield Spawn(make_value(2))
            t3 = yield Spawn(make_value(3))
            results = yield Gather(t1, t2, t3)
            return results

        result = await async_run(program(), async_handlers_preset)
        assert result.value == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_gather_preserves_order(self) -> None:
        """Gather returns results in input order, not completion order."""

        @do
        def slow():
            yield Await(asyncio.sleep(0.1))
            return "slow"

        @do
        def fast():
            return "fast"

        @do
        def program():
            t_slow = yield Spawn(slow())
            t_fast = yield Spawn(fast())
            results = yield Gather(t_slow, t_fast)
            return results

        result = await async_run(program(), async_handlers_preset)
        assert result.value == ["slow", "fast"]

    @pytest.mark.asyncio
    async def test_gather_fail_fast(self) -> None:
        """Gather fails immediately when any Future fails."""

        @do
        def failing():
            raise ValueError("gather failed")

        @do
        def slow():
            yield Await(asyncio.sleep(1.0))
            return "slow"

        @do
        def program():
            t1 = yield Spawn(failing())
            t2 = yield Spawn(slow())
            results = yield Gather(t1, t2)
            return results

        result = await async_run(program(), async_handlers_preset)
        assert result.is_err
        assert "gather failed" in str(result.error)

    @pytest.mark.asyncio
    async def test_gather_empty_returns_empty_list(self) -> None:
        """Gather with no arguments returns empty list."""

        @do
        def program():
            results = yield Gather()
            return results

        result = await async_run(program(), async_handlers_preset)
        assert result.value == []

    def test_gather_accepts_program_objects(self) -> None:
        """Gather now accepts both Waitable and Program objects.

        This was changed to support auto-unwrapping of Program arguments
        in KleisliProgramCall.to_generator().
        """

        @do
        def child():
            return 42

        # Should not raise - Gather now accepts Programs
        effect = Gather(child(), child())
        assert len(effect.items) == 2

    def test_gather_accepts_mixed_future_and_program(self) -> None:
        """Gather accepts mixing Future and Program objects.

        The handler will convert Programs to Tasks as needed.
        """
        from doeff.effects.spawn import Task

        @do
        def child():
            return 42

        # Create a Task (which implements Future protocol) for testing
        mock_task: Task[int] = Task(backend="thread", _handle="test-handle")

        # Should not raise - Gather now accepts both
        effect = Gather(mock_task, child())
        assert len(effect.items) == 2

    def test_gather_rejects_non_waitable_non_program_types(self) -> None:
        """Gather rejects types that are neither Waitable nor Program."""
        with pytest.raises(TypeError) as exc_info:
            Gather(1, 2, 3)  # type: ignore

        assert "Waitable" in str(exc_info.value) and "Program" in str(exc_info.value)


class TestPromiseEffects:
    @pytest.mark.asyncio
    async def test_create_promise_returns_promise(self) -> None:
        """CreatePromise returns a Promise."""

        @do
        def program():
            promise = yield CreatePromise()
            return isinstance(promise, Promise)

        result = await async_run(program(), async_handlers_preset)
        assert result.value is True

    @pytest.mark.asyncio
    async def test_promise_has_future(self) -> None:
        """Promise.future is a Future."""

        @do
        def program():
            promise = yield CreatePromise()
            return isinstance(promise.future, Future)

        result = await async_run(program(), async_handlers_preset)
        assert result.value is True

    @pytest.mark.asyncio
    async def test_complete_promise_resolves_wait(self) -> None:
        """CompletePromise resolves Wait on the Future."""

        @do
        def program():
            promise = yield CreatePromise()
            yield CompletePromise(promise, 42)
            result = yield Wait(promise.future)
            return result

        result = await async_run(program(), async_handlers_preset)
        assert result.value == 42

    @pytest.mark.asyncio
    async def test_fail_promise_propagates_error(self) -> None:
        """FailPromise causes Wait to raise error."""

        @do
        def program():
            promise = yield CreatePromise()
            yield FailPromise(promise, ValueError("promise failed"))
            result = yield Wait(promise.future)
            return result

        result = await async_run(program(), async_handlers_preset)
        assert result.is_err
        assert "promise failed" in str(result.error)

    @pytest.mark.asyncio
    async def test_wait_blocks_until_complete(self) -> None:
        """Wait on Promise.future blocks until CompletePromise."""

        @do
        def completer(promise: Promise):
            yield Await(asyncio.sleep(0.05))
            yield CompletePromise(promise, "resolved")

        @do
        def program():
            promise = yield CreatePromise()
            _ = yield Spawn(completer(promise))
            result = yield Wait(promise.future)
            return result

        result = await async_run(program(), async_handlers_preset)
        assert result.value == "resolved"

    @pytest.mark.asyncio
    async def test_multiple_waiters_on_same_future(self) -> None:
        """Multiple tasks can Wait on the same Future."""

        @do
        def waiter(future: Future):
            return (yield Wait(future))

        @do
        def program():
            promise = yield CreatePromise()
            t1 = yield Spawn(waiter(promise.future))
            t2 = yield Spawn(waiter(promise.future))
            yield CompletePromise(promise, "shared")
            r1 = yield Wait(t1)
            r2 = yield Wait(t2)
            return (r1, r2)

        result = await async_run(program(), async_handlers_preset)
        assert result.value == ("shared", "shared")

    @pytest.mark.asyncio
    async def test_complete_already_completed_promise_raises(self) -> None:
        """CompletePromise on already-completed Promise raises RuntimeError."""

        @do
        def program():
            promise = yield CreatePromise()
            yield CompletePromise(promise, "first")
            yield CompletePromise(promise, "second")  # Should raise
            return "should not reach"

        result = await async_run(program(), async_handlers_preset)
        assert result.is_err
        assert isinstance(result.error, RuntimeError)
        assert "already completed" in str(result.error).lower()

    @pytest.mark.asyncio
    async def test_fail_already_completed_promise_raises(self) -> None:
        """FailPromise on already-completed Promise raises RuntimeError."""

        @do
        def program():
            promise = yield CreatePromise()
            yield CompletePromise(promise, "value")
            yield FailPromise(promise, ValueError("error"))  # Should raise
            return "should not reach"

        result = await async_run(program(), async_handlers_preset)
        assert result.is_err
        assert isinstance(result.error, RuntimeError)
        assert "already completed" in str(result.error).lower()

    @pytest.mark.asyncio
    async def test_complete_already_failed_promise_raises(self) -> None:
        """CompletePromise on already-failed Promise raises RuntimeError."""

        @do
        def program():
            promise = yield CreatePromise()
            yield FailPromise(promise, ValueError("first error"))
            yield CompletePromise(promise, "value")  # Should raise
            return "should not reach"

        result = await async_run(program(), async_handlers_preset)
        assert result.is_err
        assert isinstance(result.error, RuntimeError)
        assert "already completed" in str(result.error).lower()

    @pytest.mark.asyncio
    async def test_fail_already_failed_promise_raises(self) -> None:
        """FailPromise on already-failed Promise raises RuntimeError."""

        @do
        def program():
            promise = yield CreatePromise()
            yield FailPromise(promise, ValueError("first"))
            yield FailPromise(promise, ValueError("second"))  # Should raise
            return "should not reach"

        result = await async_run(program(), async_handlers_preset)
        assert result.is_err
        assert isinstance(result.error, RuntimeError)
        assert "already completed" in str(result.error).lower()


class TestConcurrencyComposition:
    @pytest.mark.asyncio
    async def test_race_with_timeout_pattern(self) -> None:
        """Race can implement timeout pattern."""

        @do
        def slow_work():
            yield Await(asyncio.sleep(10.0))
            return "completed"

        @do
        def timeout(seconds: float):
            yield Await(asyncio.sleep(seconds))
            return None

        @do
        def with_timeout():
            work = yield Spawn(slow_work())
            timer = yield Spawn(timeout(0.01))

            result = yield Race(work, timer)

            for loser in result.rest:
                yield loser.cancel()

            if result.first is timer:
                return "timeout"
            return result.value

        result = await async_run(with_timeout(), async_handlers_preset)
        assert result.value == "timeout"

    @pytest.mark.asyncio
    async def test_spawn_wait_is_like_sequential(self) -> None:
        """Spawn + immediate Wait is like sequential execution."""

        @do
        def step1():
            return 1

        @do
        def step2(x: int):
            return x + 1

        @do
        def program():
            t1 = yield Spawn(step1())
            r1 = yield Wait(t1)

            t2 = yield Spawn(step2(r1))
            r2 = yield Wait(t2)

            return r2

        result = await async_run(program(), async_handlers_preset)
        assert result.value == 2

    @pytest.mark.asyncio
    async def test_parallel_then_sequential(self) -> None:
        """Start work in parallel, then process results sequentially."""

        @do
        def fetch(name: str):
            return f"data_{name}"

        @do
        def program():
            t1 = yield Spawn(fetch("a"))
            t2 = yield Spawn(fetch("b"))
            t3 = yield Spawn(fetch("c"))

            results = yield Gather(t1, t2, t3)
            return "-".join(results)

        result = await async_run(program(), async_handlers_preset)
        assert result.value == "data_a-data_b-data_c"
