from __future__ import annotations

from doeff import (
    AcquireSemaphore,
    CreateSemaphore,
    Pure,
    ReleaseSemaphore,
    Spawn,
    Try,
    Wait,
    default_handlers,
    do,
    run,
)
from doeff.effects import TaskCancelledError
from doeff.effects.spawn import Task
from doeff.program import Program
from doeff.types import EffectBase


def test_cancel_yields_effect() -> None:
    """Task.cancel() must yield an EffectBase, not Program.pure()."""
    task = Task(_handle={"type": "Task", "task_id": 0})
    result = task.cancel()

    assert isinstance(result, EffectBase)
    assert not isinstance(result, Program)


def test_no_python_side_cancel_state() -> None:
    """Cancellation state should live in Rust scheduler state, not Python globals."""
    import doeff.effects.spawn as spawn_mod

    assert not hasattr(spawn_mod, "_cancelled_task_ids")


def test_cancel_pending_task() -> None:
    """Cancelling a pending task should mark it cancelled immediately."""

    @do
    def program():
        task = yield Spawn(Pure("never runs"))
        _ = yield task.cancel()
        return (yield Try(Wait(task)))

    result = run(program(), handlers=default_handlers())
    assert result.is_ok()
    assert result.value.is_err()
    assert isinstance(result.value.error, TaskCancelledError)


def test_cancel_running_task_cooperative() -> None:
    """Cancelling a running task should fail Wait with TaskCancelledError."""

    @do
    def cooperative_spin():
        for _ in range(10_000):
            yield Pure(None)
        return "finished"

    @do
    def program():
        task = yield Spawn(cooperative_spin())
        yield Pure(None)
        _ = yield task.cancel()
        return (yield Try(Wait(task)))

    result = run(program(), handlers=default_handlers())
    assert result.is_ok()
    assert result.value.is_err()
    assert isinstance(result.value.error, TaskCancelledError)


def test_cancel_cleans_semaphore_waiters() -> None:
    """Cancelling a blocked semaphore waiter should remove it immediately."""

    @do
    def waiter(sem):
        yield AcquireSemaphore(sem)
        yield ReleaseSemaphore(sem)

    @do
    def program():
        sem = yield CreateSemaphore(1)
        yield AcquireSemaphore(sem)

        blocked = yield Spawn(waiter(sem))
        yield Pure(None)
        _ = yield blocked.cancel()

        yield ReleaseSemaphore(sem)
        yield AcquireSemaphore(sem)
        yield ReleaseSemaphore(sem)
        return (yield Try(Wait(blocked)))

    result = run(program(), handlers=default_handlers())
    assert result.is_ok()
    assert result.value.is_err()
    assert isinstance(result.value.error, TaskCancelledError)
