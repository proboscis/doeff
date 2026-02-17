from __future__ import annotations

import doeff_vm

from doeff import (
    AcquireSemaphore,
    CreatePromise,
    CreateSemaphore,
    Pure,
    ReleaseSemaphore,
    Safe,
    Spawn,
    Wait,
    default_handlers,
    do,
    run,
)
from doeff.effects import TaskCancelledError
from doeff.effects.spawn import Task
from doeff.program import Program


def test_cancel_yields_effect() -> None:
    """Task.cancel() must return a scheduler effect, not Program.pure()."""
    task = Task(backend="thread", _handle={"type": "Task", "task_id": 0})

    result = task.cancel()

    assert not isinstance(result, Program)
    assert isinstance(result, doeff_vm.PyCancelEffect)


def test_no_python_side_cancel_state() -> None:
    """Cancellation state must not live in doeff.effects.spawn globals."""
    import doeff.effects.spawn as spawn_mod

    assert not hasattr(spawn_mod, "_cancelled_task_ids")


def test_cancel_pending_task() -> None:
    """Cancelling a pending task must resolve Wait(task) with TaskCancelledError."""

    @do
    def program():
        task = yield Spawn(Pure("never runs"))
        _ = yield task.cancel()
        result = yield Safe(Wait(task))
        return result

    result = run(program(), handlers=default_handlers())

    assert result.is_ok()
    assert result.value.is_err()
    assert isinstance(result.value.error, TaskCancelledError)


def test_cancel_running_task_cooperative() -> None:
    """Running-task cancellation should be cooperative at the next scheduler effect."""
    holder: dict[str, Task[str]] = {}

    @do
    def worker():
        _ = yield holder["task"].cancel()
        _ = yield CreatePromise()
        return "unreachable"

    @do
    def program():
        task = yield Spawn(worker())
        holder["task"] = task
        result = yield Safe(Wait(task))
        return result

    result = run(program(), handlers=default_handlers())

    assert result.is_ok()
    assert result.value.is_err()
    assert isinstance(result.value.error, TaskCancelledError)


def test_cancel_cleans_semaphore_waiters() -> None:
    """Cancel must remove blocked semaphore waiters immediately at cancel time."""

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

        blocked = yield Spawn(waiter(sem, "blocked"))
        second = yield Spawn(waiter(sem, "second"))
        third = yield Spawn(waiter(sem, "third"))
        _ = yield blocked.cancel()

        yield ReleaseSemaphore(sem)
        _ = yield Wait(second)
        _ = yield Wait(third)

        result = yield Safe(Wait(blocked))
        return wake_order, result

    result = run(program(), handlers=default_handlers())

    assert result.is_ok()
    order, blocked_result = result.value
    assert order == ["second", "third"]
    assert blocked_result.is_err()
    assert isinstance(blocked_result.error, TaskCancelledError)
