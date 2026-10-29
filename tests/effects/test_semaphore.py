from __future__ import annotations

import gc

import pytest
from doeff_core_effects import SchedulerImplementation, scheduled

from doeff import (
    AcquireSemaphore,
    CreateSemaphore,
    Gather,
    ReleaseSemaphore,
    Semaphore,
    Spawn,
    Wait,
    do,
    run,
)
from tests._run_helpers import run_with_defaults


def test_semaphore_public_handle_has_no_runtime_cleanup_surface() -> None:
    import doeff_vm

    @do
    def program():
        return (yield CreateSemaphore(1))

    result = run_with_defaults(program())
    assert result.is_ok()
    semaphore = result.value
    assert not hasattr(type(semaphore), "__del__")
    assert not hasattr(semaphore, "_scheduler_state_id")
    assert not hasattr(semaphore, "_cleanup_on_del")
    assert not hasattr(doeff_vm, "_register_semaphore_handle")
    assert not hasattr(doeff_vm, "_notify_semaphore_handle_dropped")



def test_semaphore_constructor_rejects_runtime_private_kwargs() -> None:
    with pytest.raises(TypeError):
        Semaphore(id=11, _scheduler_state_id=17)  # type: ignore[call-arg]


@pytest.mark.parametrize("args", [(0,), (1,), (3,), ("x",)])
def test_semaphore_direct_construction_raises_type_error(args: tuple[object, ...]) -> None:
    """A hand-made handle names no semaphore of the run (KeyError at Acquire) or shares the
    permits of the run's semaphore with the same number, so the base class refuses to be built
    directly (docs/09, docs/13, docs/21, SPEC-EFF-014)."""
    with pytest.raises(TypeError, match=r"sem = yield CreateSemaphore\(permits\)"):
        Semaphore(*args)


def test_semaphore_subclass_minted_by_a_create_semaphore_handler_calls_base_init() -> None:
    """A handler that answers CreateSemaphore itself (doeff-cluster's ClusterSemaphore and
    ReplaySemaphore) returns its own subclass handle, built through Semaphore.__init__."""

    class NamedSemaphore(Semaphore):
        def __init__(self, name: str) -> None:
            super().__init__(f"named:{name}")
            self.name = name

    handle = NamedSemaphore("jobs")
    assert isinstance(handle, Semaphore)
    assert handle.sem_id == "named:jobs"


@pytest.mark.parametrize("implementation", ["python", "rust"])
def test_create_semaphore_handle_is_the_exact_semaphore_type(
    implementation: SchedulerImplementation,
) -> None:
    """Both schedulers mint the base Semaphore without going through the refused constructor."""

    @do
    def program():
        sem = yield CreateSemaphore(2)
        yield AcquireSemaphore(sem)
        yield ReleaseSemaphore(sem)
        return sem

    sem = run(scheduled(program(), implementation=implementation))
    assert type(sem) is Semaphore
    assert repr(sem) == f"Semaphore({sem.sem_id})"


class TestSemaphoreEffectContract:
    def test_semaphore_effects_importable(self) -> None:
        """CreateSemaphore, AcquireSemaphore, ReleaseSemaphore must be importable."""
        from doeff import AcquireSemaphore, CreateSemaphore, ReleaseSemaphore

        assert callable(CreateSemaphore)
        assert callable(AcquireSemaphore)
        assert callable(ReleaseSemaphore)



class TestSemaphoreRuntimeBehavior:
    def test_create_and_immediate_acquire_release(self) -> None:
        """Binary semaphore: acquire succeeds immediately, release returns permit."""

        @do
        def program():
            sem = yield CreateSemaphore(1)
            yield AcquireSemaphore(sem)
            yield ReleaseSemaphore(sem)
            return "done"

        result = run_with_defaults(program())
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

        result = run_with_defaults(program())
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

        result = run_with_defaults(program())
        assert result.is_ok()
        assert result.value == ["first", "second", "third"]

    def test_counting_semaphore_allows_n_concurrent(self) -> None:
        """CreateSemaphore(3) allows 3 tasks to hold permits simultaneously."""
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

        result = run_with_defaults(program())
        assert result.is_ok()
        assert result.value <= 3

    def test_over_release_raises_runtime_error(self) -> None:
        """Releasing more times than max permits must raise RuntimeError."""

        @do
        def program():
            sem = yield CreateSemaphore(1)
            yield ReleaseSemaphore(sem)
            return "should not reach"

        result = run_with_defaults(program())
        assert result.is_err()
        assert isinstance(result.error, RuntimeError)
        assert "released too many" in str(result.error).lower()

    def test_create_zero_permits_raises_value_error(self) -> None:
        """CreateSemaphore(0) must raise ValueError."""

        @do
        def program():
            yield CreateSemaphore(0)
            return "should not reach"

        result = run_with_defaults(program())
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

        result = run_with_defaults(program())
        assert result.is_ok()
        assert result.value == ["first-waiter", "late-acquirer"]

    def test_live_reference_alias_keeps_semaphore_valid_after_original_name_drop(self) -> None:
        """A second Python reference to the same runtime handle keeps it alive."""

        @do
        def tick():
            return None
            yield

        @do
        def program():
            sem = yield CreateSemaphore(1)
            alias = sem

            del sem
            gc.collect()

            warmup = yield Spawn(tick())
            _ = yield Wait(warmup)

            yield AcquireSemaphore(alias)
            yield ReleaseSemaphore(alias)
            return "alias-still-valid"

        result = run_with_defaults(program())
        assert result.is_ok()
        assert result.value == "alias-still-valid"

    def test_live_reference_alias_keeps_waiters_releasable_after_original_name_drop(self) -> None:
        """A second Python reference to the same runtime handle keeps waiters releasable."""

        @do
        def waiter(alias: Semaphore):
            yield AcquireSemaphore(alias)
            yield ReleaseSemaphore(alias)
            return "waiter-acquired"

        @do
        def tick():
            return None
            yield

        @do
        def program():
            sem = yield CreateSemaphore(1)
            alias = sem
            yield AcquireSemaphore(sem)

            waiter_task = yield Spawn(waiter(alias))

            warmup = yield Spawn(tick())
            _ = yield Wait(warmup)

            del sem
            gc.collect()

            yield ReleaseSemaphore(alias)
            waiter_result = yield Wait(waiter_task)
            return waiter_result

        result = run_with_defaults(program())
        assert result.is_ok()
        assert result.value == "waiter-acquired"
