"""Queue effects for cooperative scheduling.

These effects are used internally by the scheduler_handler to manage
the task queue and waiters. They are handled by queue_handler, which
uses store primitives to maintain scheduling state.

These effects are NOT meant to be used directly by user programs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from doeff._types_internal import EffectBase

if TYPE_CHECKING:
    from doeff.cesk.frames import Kontinuation
    from doeff.effects.spawn import Task

T = TypeVar("T")


@dataclass(frozen=True, kw_only=True)
class QueueAdd(EffectBase):
    """Add a task to the queue.
    
    Used by scheduler_handler to enqueue tasks for cooperative scheduling.
    
    Attributes:
        task_id: Unique identifier for the task
        k: The continuation to add to the queue (legacy, use program instead)
        store_snapshot: Isolated store for spawned tasks (None for main task)
        program: The program to run (preferred over k)
    """
    task_id: Any
    k: Kontinuation
    store_snapshot: dict[str, Any] | None = None
    program: Any | None = None


@dataclass(frozen=True, kw_only=True)
class QueuePop(EffectBase):
    """Pop next continuation from the task queue.
    
    Returns a tuple of (task_id, k, store_snapshot) or None if queue is empty.
    """
    pass


@dataclass(frozen=True, kw_only=True)
class QueueIsEmpty(EffectBase):
    """Check if the task queue is empty.
    
    Returns True if no tasks are waiting to run.
    """
    pass


@dataclass(frozen=True, kw_only=True)
class RegisterWaiter(EffectBase):
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
    waiter_k: Kontinuation
    waiter_store: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class TaskComplete(EffectBase):
    """Mark a task as complete with a result.
    
    This triggers wake-up of any registered waiters.
    
    Attributes:
        handle_id: The task's handle ID
        task_id: The internal task ID
        result: The task's return value
        error: The task's exception (None on success)
        store_snapshot: The task's final store (for isolated tasks)
    """
    handle_id: Any
    task_id: Any
    result: Any = None
    error: BaseException | None = None
    store_snapshot: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class GetTaskResult(EffectBase):
    """Get the result of a completed task.
    
    Attributes:
        handle_id: The task's handle ID
        
    Returns a tuple of (is_complete, result, error) or None if task not found.
    """
    handle_id: Any


@dataclass(frozen=True, kw_only=True)
class CreateTaskHandle(EffectBase):
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
class CancelTask(EffectBase):
    """Request cancellation of a task.
    
    Attributes:
        handle_id: The task's handle ID
        
    Returns True if task was cancelled, False if already complete or not found.
    """
    handle_id: Any


@dataclass(frozen=True, kw_only=True)
class IsTaskDone(EffectBase):
    """Check if a task is complete.
    
    Attributes:
        handle_id: The task's handle ID
        
    Returns True if task is complete (success, failure, or cancelled).
    """
    handle_id: Any


@dataclass(frozen=True, kw_only=True)
class CreatePromiseHandle(EffectBase):
    """Create a new promise handle.
    
    Returns a tuple of (handle_id, Promise).
    """
    pass


@dataclass(frozen=True, kw_only=True)
class GetCurrentTaskId(EffectBase):
    """Get the current task's ID.
    
    Returns the task_id of the currently executing task.
    """
    pass


@dataclass(frozen=True, kw_only=True)
class GetCurrentTaskStore(EffectBase):
    """Get the current task's isolated store (for spawned tasks).
    
    Returns the store snapshot if this is a spawned task, None for main task.
    """
    task_id: Any


@dataclass(frozen=True, kw_only=True)
class UpdateTaskStore(EffectBase):
    """Update a spawned task's isolated store.
    
    Attributes:
        task_id: The task's ID
        store: The new store snapshot
    """
    task_id: Any
    store: dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class SetTaskSuspended(EffectBase):
    """Signal that current task should suspend (waiting for another task).
    
    The outer scheduler loop will check for this and switch to another task.
    
    Attributes:
        task_id: The task that is suspending
        waiting_for: The handle_id of the task/promise being waited on
    """
    task_id: Any
    waiting_for: Any


@dataclass(frozen=True, kw_only=True)
class TaskCompletedEffect(EffectBase):
    """Spawned task completed - handler intercepts to switch to next task.
    
    Spawned programs are wrapped to yield this effect instead of returning.
    The scheduler_handler intercepts this, records the result, wakes waiters,
    and uses ResumeK to switch to the next task.
    
    Attributes:
        task_id: The task that completed
        handle_id: The task's handle for waiter lookup
        result: The task's return value
        error: The task's exception (None on success)
    """
    task_id: Any
    handle_id: Any
    result: Any = None
    error: BaseException | None = None


@dataclass(frozen=True, kw_only=True)
class SuspendForIOEffect(EffectBase):
    """Signal that current task needs to suspend for async I/O.
    
    This is yielded by async_effects_handler instead of returning SuspendOn.
    The scheduler_handler intercepts this:
    1. Stores the awaitable with the current task's continuation
    2. Switches to another task if available
    3. If no other tasks, propagates as SuspendOn with all pending awaitables
    
    Attributes:
        awaitable: The async awaitable to wait for
        resume_k: The full continuation to use when resuming this task
    """
    awaitable: Any
    resume_k: Any = None


@dataclass(frozen=True, kw_only=True)
class AddPendingIO(EffectBase):
    """Add a task to the pending I/O list."""
    task_id: Any
    awaitable: Any
    k: Any
    store_snapshot: dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class GetPendingIO(EffectBase):
    """Get all pending I/O tasks. Returns dict[task_id, (awaitable, k, store)]."""
    pass


@dataclass(frozen=True, kw_only=True)
class RemovePendingIO(EffectBase):
    """Remove a task from pending I/O after completion."""
    task_id: Any


@dataclass(frozen=True, kw_only=True)
class ResumePendingIO(EffectBase):
    """Resume a pending I/O task with a value or error."""
    task_id: Any
    value: Any = None
    error: BaseException | None = None


__all__ = [
    "AddPendingIO",
    "CancelTask",
    "CreatePromiseHandle",
    "CreateTaskHandle",
    "GetCurrentTaskId",
    "GetCurrentTaskStore",
    "GetPendingIO",
    "GetTaskResult",
    "IsTaskDone",
    "QueueAdd",
    "QueueIsEmpty",
    "QueuePop",
    "RegisterWaiter",
    "RemovePendingIO",
    "ResumePendingIO",
    "SetTaskSuspended",
    "SuspendForIOEffect",
    "TaskComplete",
    "TaskCompletedEffect",
    "UpdateTaskStore",
]
