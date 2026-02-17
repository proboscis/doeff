"""Scheduler-internal effects for cooperative scheduling.

These effects are used internally by the task_scheduler_handler to manage
the task queue and waiters. They are handled by scheduler_state_handler, which
uses store primitives to maintain scheduling state.

These effects are NOT meant to be used directly by user programs.
The underscore prefix (_Scheduler*) signals internal/private use.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import doeff_vm

from doeff._types_internal import EffectBase

T = TypeVar("T")


@dataclass(frozen=True, kw_only=True)
class _SchedulerEnqueueTask(EffectBase):
    """Add a task to the queue.

    Used by task_scheduler_handler to enqueue tasks for cooperative scheduling.

    Attributes:
        task_id: Unique identifier for the task
        k: The continuation to add to the queue (legacy, use program instead)
        store_snapshot: Isolated store for spawned tasks (None for main task)
        program: The program to run (preferred over k)
    """

    task_id: Any
    k: Any
    store_snapshot: dict[str, Any] | None = None
    program: Any | None = None


@dataclass(frozen=True, kw_only=True)
class _SchedulerDequeueTask(EffectBase):
    """Pop next continuation from the task queue.

    Returns a tuple of (task_id, k, store_snapshot) or None if queue is empty.
    """

    pass


@dataclass(frozen=True, kw_only=True)
class _SchedulerRegisterWaiter(EffectBase):
    """Register a continuation to wake when a task/promise completes.

    When the specified task completes (success or failure), the waiter
    continuation will be added back to the ready queue.

    Attributes:
        handle_id: The handle ID to wait on (Task or Promise handle)
        waiter_task_id: The task ID of the waiting task
        waiter_k: The continuation to resume when complete
        waiter_store: The waiter's store at suspension time (for isolation)
    """

    handle_id: Any
    waiter_task_id: Any
    waiter_k: Any
    waiter_store: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class _SchedulerTaskComplete(EffectBase):
    """Mark a task as complete with a result.

    This triggers wake-up of any registered waiters.

    Attributes:
        handle_id: The task's handle ID
        task_id: The internal task ID
        result: Canonical task result payload (Ok(value) or Err(error))
        store_snapshot: The task's final store (for isolated tasks)
    """

    handle_id: Any
    task_id: Any
    result: Any
    store_snapshot: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class _SchedulerGetTaskResult(EffectBase):
    """Get the result of a completed task.

    Attributes:
        handle_id: The task's handle ID

    Returns a tuple of (is_complete, result, error) or None if task not found.
    """

    handle_id: Any


@dataclass(frozen=True, kw_only=True)
class _SchedulerCreateTaskHandle(EffectBase):
    """Create a new task handle for tracking a spawned task.

    Attributes:
        task_id: The internal task ID
        env_snapshot: The environment snapshot
        store_snapshot: The store snapshot

    Returns a new Task handle.
    """

    task_id: Any
    env_snapshot: dict[Any, Any]
    store_snapshot: dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class _SchedulerCancelTask(EffectBase):
    """Request cancellation of a task.

    Attributes:
        handle_id: The task's handle ID

    Returns True if task was cancelled, False if already complete or not found.
    """

    handle_id: Any


@dataclass(frozen=True, kw_only=True)
class _SchedulerCreatePromise(EffectBase):
    """Create a new promise handle.

    Returns a tuple of (handle_id, Promise).
    """

    __doeff_scheduler_create_promise__ = True

    pass


@dataclass(frozen=True, kw_only=True)
class _SchedulerGetCurrentTaskId(EffectBase):
    """Get the current task's ID.

    Returns the task_id of the currently executing task.
    """

    pass


_SchedulerTaskCompleted = doeff_vm._SchedulerTaskCompleted


@dataclass(frozen=True, kw_only=True)
class WaitForExternalCompletion(EffectBase):
    """Request blocking wait for external completion queue.

    Yielded by task_scheduler_handler when:
    - Task queue is empty (no runnable doeff tasks)
    - External promises are pending (asyncio tasks running)

    Handled by:
    - sync_external_wait_handler: blocking queue.get()
    - async_external_wait_handler: PythonAsyncSyntaxEscape with run_in_executor

    See SPEC-SCHED-001 for architecture.

    Attributes:
        queue: The external completion queue (queue.Queue)
    """

    queue: Any  # queue.Queue - can't import due to circular deps


def sync_external_wait_handler(effect: Any, k: Any):
    """Handle WaitForExternalCompletion with blocking queue.get()."""
    if isinstance(effect, WaitForExternalCompletion):
        effect.queue.get(block=True)
        return (yield doeff_vm.Resume(k, None))

    yield doeff_vm.Delegate()


def async_external_wait_handler(effect: Any, k: Any):
    """Handle WaitForExternalCompletion with PythonAsyncSyntaxEscape.

    Uses run_in_executor so queue waiting does not block the active event loop.
    """
    if isinstance(effect, WaitForExternalCompletion):

        async def _wait_one() -> None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, effect.queue.get, True)

        _ = yield doeff_vm.PythonAsyncSyntaxEscape(action=_wait_one)
        return (yield doeff_vm.Resume(k, None))

    yield doeff_vm.Delegate()


__all__ = [
    "_SchedulerCancelTask",
    "_SchedulerCreatePromise",
    "_SchedulerCreateTaskHandle",
    "_SchedulerDequeueTask",
    "_SchedulerEnqueueTask",
    "_SchedulerGetCurrentTaskId",
    "_SchedulerGetTaskResult",
    "_SchedulerRegisterWaiter",
    "_SchedulerTaskComplete",
    "_SchedulerTaskCompleted",
    "async_external_wait_handler",
    "sync_external_wait_handler",
    "WaitForExternalCompletion",
]
