from __future__ import annotations

import gc

import doeff_vm

from doeff import (
    AcquireSemaphore,
    CreateSemaphore,
    Gather,
    ReleaseSemaphore,
    Semaphore,
    Spawn,
    Try,
    Wait,
    default_handlers,
    do,
    run,
)
from doeff.effects import TaskCancelledError


class TestSemaphoreEffectContract:
    def test_semaphore_effects_importable(self) -> None:
        """CreateSemaphore, AcquireSemaphore, ReleaseSemaphore must be importable."""
        from doeff import AcquireSemaphore, CreateSemaphore, ReleaseSemaphore

        assert callable(CreateSemaphore)
        assert callable(AcquireSemaphore)
        assert callable(ReleaseSemaphore)

    def test_semaphore_type_importable(self) -> None:
        """Semaphore handle type must be importable."""
        from doeff import Semaphore

        assert Semaphore is not None


class TestSemaphoreRuntimeBehavior:
    def test_create_and_immediate_acquire_release(self) -> None:
        """Binary semaphore: acquire succeeds immediately, release returns permit."""

        @do
        def program():
            sem = yield CreateSemaphore(1)
            yield AcquireSemaphore(sem)
            yield ReleaseSemaphore(sem)
            return "done"

        result = run(program(), handlers=default_handlers())
        assert result.is_ok()
        assert result.value == "done"

    def test_acquire_blocks_when_no_permits(self) -> None:
        """Second acquire on binary semaphore must block until release."""
        order: list[str] = []

        @do
        def holder(sem):
            yield AcquireSemaphore(sem)
            order.append("holder-acquired")
            yield ReleaseSemaphore(sem)
            order.append("holder-released")

        @do
        def waiter(sem):
            yield AcquireSemaphore(sem)
            order.append("waiter-acquired")
            yield ReleaseSemaphore(sem)

        @do
        def program():
            sem = yield CreateSemaphore(1)
            t1 = yield Spawn(holder(sem))
            t2 = yield Spawn(waiter(sem))
            yield Gather(t1, t2)
            return order

        result = run(program(), handlers=default_handlers())
        assert result.is_ok()
        assert result.value.index("holder-acquired") < result.value.index("waiter-acquired")

    def test_fifo_fairness(self) -> None:
        """Multiple waiters must be woken in acquire order."""
        wake_order: list[str] = []

        @do
        def waiter(sem, name: str):
            yield AcquireSemaphore(sem)
            wake_order.append(name)
            yield ReleaseSemaphore(sem)

        @do
        def program():
            sem = yield CreateSemaphore(1)
            yield AcquireSemaphore(sem)

            t1 = yield Spawn(waiter(sem, "first"))
            t2 = yield Spawn(waiter(sem, "second"))
            t3 = yield Spawn(waiter(sem, "third"))

            yield ReleaseSemaphore(sem)
            yield Gather(t1, t2, t3)
            return wake_order

        result = run(program(), handlers=default_handlers())
        assert result.is_ok()
        assert result.value == ["first", "second", "third"]

    def test_counting_semaphore_allows_n_concurrent(self) -> None:
        """Semaphore(3) allows 3 tasks to hold permits simultaneously."""
        max_concurrent = {"value": 0}
        current = {"value": 0}

        @do
        def worker(sem):
            yield AcquireSemaphore(sem)
            current["value"] += 1
            max_concurrent["value"] = max(max_concurrent["value"], current["value"])
            current["value"] -= 1
            yield ReleaseSemaphore(sem)

        @do
        def program():
            sem = yield CreateSemaphore(3)
            tasks = []
            for _ in range(6):
                t = yield Spawn(worker(sem))
                tasks.append(t)
            yield Gather(*tasks)
            return max_concurrent["value"]

        result = run(program(), handlers=default_handlers())
        assert result.is_ok()
        assert result.value <= 3

    def test_over_release_raises_runtime_error(self) -> None:
        """Releasing more times than max permits must raise RuntimeError."""

        @do
        def program():
            sem = yield CreateSemaphore(1)
            yield ReleaseSemaphore(sem)
            return "should not reach"

        result = run(program(), handlers=default_handlers())
        assert result.is_err()
        assert isinstance(result.error, RuntimeError)
        assert "released too many" in str(result.error).lower()

    def test_create_zero_permits_raises_value_error(self) -> None:
        """CreateSemaphore(0) must raise ValueError."""

        @do
        def program():
            yield CreateSemaphore(0)
            return "should not reach"

        result = run(program(), handlers=default_handlers())
        assert result.is_err()
        assert isinstance(result.error, ValueError)

    def test_permit_transfer_no_stealing(self) -> None:
        """When waiter exists, released permit goes to waiter, not a new acquirer."""
        order: list[str] = []

        @do
        def first_waiter(sem):
            yield AcquireSemaphore(sem)
            order.append("first-waiter")
            yield ReleaseSemaphore(sem)

        @do
        def late_acquirer(sem):
            yield AcquireSemaphore(sem)
            order.append("late-acquirer")
            yield ReleaseSemaphore(sem)

        @do
        def program():
            sem = yield CreateSemaphore(1)
            yield AcquireSemaphore(sem)

            t1 = yield Spawn(first_waiter(sem))
            t2 = yield Spawn(late_acquirer(sem))

            yield ReleaseSemaphore(sem)
            yield Gather(t1, t2)
            return order

        result = run(program(), handlers=default_handlers())
        assert result.is_ok()
        assert result.value == ["first-waiter", "late-acquirer"]

    def test_cancelled_waiter_is_removed_and_order_is_preserved(self) -> None:
        """Cancelled waiters are skipped and remaining waiters wake FIFO."""
        wake_order: list[str] = []

        @do
        def waiter(sem, name: str):
            yield AcquireSemaphore(sem)
            wake_order.append(name)
            yield ReleaseSemaphore(sem)

        @do
        def program():
            sem = yield CreateSemaphore(1)
            yield AcquireSemaphore(sem)

            cancelled = yield Spawn(waiter(sem, "cancelled"))
            second = yield Spawn(waiter(sem, "second"))
            third = yield Spawn(waiter(sem, "third"))

            _ = yield cancelled.cancel()
            yield ReleaseSemaphore(sem)
            yield Gather(second, third)

            cancelled_result = yield Try(Wait(cancelled))
            return wake_order, cancelled_result

        result = run(program(), handlers=default_handlers())
        assert result.is_ok()
        wake_order, cancelled_result = result.value
        assert wake_order == ["second", "third"]
        assert cancelled_result.is_err()
        assert isinstance(cancelled_result.error, TaskCancelledError)

    def test_semaphore_state_cleaned_after_handle_dropped(self) -> None:
        """GC of semaphore handles should clean up scheduler internal semaphore state."""

        @do
        def tick():
            return None
            yield

        @do
        def program():
            semaphores = []
            for _ in range(8):
                sem = yield CreateSemaphore(1)
                semaphores.append(sem)

            state_id = semaphores[0]._scheduler_state_id
            before = doeff_vm._debug_scheduler_semaphore_count(state_id)
            semaphores.clear()
            del sem
            gc.collect()
            # Force one scheduler cycle so VM-internal temporary references are released.
            task = yield Spawn(tick())
            _ = yield Wait(task)
            after = doeff_vm._debug_scheduler_semaphore_count(state_id)
            return before, after

        result = run(program(), handlers=default_handlers())
        assert result.is_ok()
        before, after = result.value
        assert before == 8
        assert after == 0

    def test_semaphore_cleanup_with_pending_waiters(self) -> None:
        """Dropping the owner handle while waiters are pending yields waiter failure + cleanup."""

        @do
        def holder(semaphore_id: int):
            yield AcquireSemaphore(Semaphore(semaphore_id))
            return "held"

        @do
        def waiter(semaphore_id: int):
            yield AcquireSemaphore(Semaphore(semaphore_id))
            return "waiter-acquired"

        @do
        def tick():
            return None
            yield

        @do
        def program():
            sem = yield CreateSemaphore(1)
            state_id = sem._scheduler_state_id
            holder_task = yield Spawn(holder(sem.id))
            waiter_task = yield Spawn(waiter(sem.id))

            _ = yield Try(Wait(holder_task))

            # Let waiter attempt acquire and block while owner handle is still alive.
            warmup = yield Spawn(tick())
            _ = yield Try(Wait(warmup))

            del sem
            gc.collect()

            # Trigger scheduler-side drop notification handling.
            trigger = yield Spawn(tick())
            _ = yield Try(Wait(trigger))

            waiter_result = yield Try(Wait(waiter_task))
            remaining = doeff_vm._debug_scheduler_semaphore_count(state_id)
            return waiter_result, remaining

        result = run(program(), handlers=default_handlers())
        assert result.is_ok()
        waiter_result, remaining = result.value
        assert waiter_result.is_err()
        assert remaining == 0
