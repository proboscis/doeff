"""Event types for the CESK machine.

Events are what step() emits to tell the runtime what happened.
This follows the pattern: Handler → Action → step() → Event → Runtime

Event types:
- TaskDone: Task completed successfully with value
- TaskFailed: Task failed with exception
- TaskBlocked: Task is waiting on external I/O
- TaskWaiting: Task is waiting on a condition (future, time)
- TaskCreated: A new task was created
- TaskCancelled: A task was cancelled
- FutureResolved: A future got its value
- FutureRejected: A future got an error
- IORequested: I/O operation needs to be executed
- TimeWaitRequested: Task waiting until specific time
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable

from doeff.cesk.types import Environment, FutureId, Store, TaskId

if TYPE_CHECKING:
    from doeff.cesk_traceback import CapturedTraceback
    from doeff.program import Program


# ============================================================================
# Task Lifecycle Events
# ============================================================================


@dataclass(frozen=True)
class TaskDone:
    """Task completed successfully.

    The task's computation finished with a value.
    If the task has an associated future, the future should be resolved.
    """

    task_id: TaskId
    value: Any
    store: Store


@dataclass(frozen=True)
class TaskFailed:
    """Task failed with exception.

    The task's computation raised an unhandled exception.
    If the task has an associated future, the future should be rejected.
    """

    task_id: TaskId
    error: BaseException
    store: Store
    captured_traceback: CapturedTraceback | None = None


@dataclass(frozen=True)
class TaskCancelled:
    """Task was cancelled.

    The task was stopped (e.g., by race when another task won).
    """

    task_id: TaskId


# ============================================================================
# Task Blocking Events
# ============================================================================


@dataclass(frozen=True)
class TaskBlocked:
    """Task is blocked waiting on external I/O.

    Runtime needs to execute the I/O and resume the task.
    """

    task_id: TaskId
    io_operation: Any  # The I/O to perform


@dataclass(frozen=True)
class TaskWaitingOnFuture:
    """Task is waiting for a future to complete."""

    task_id: TaskId
    future_id: FutureId


@dataclass(frozen=True)
class TaskWaitingOnFutures:
    """Task is waiting for multiple futures (gather semantics)."""

    task_id: TaskId
    future_ids: tuple[FutureId, ...]


@dataclass(frozen=True)
class TaskRacing:
    """Task is racing on multiple futures (race semantics)."""

    task_id: TaskId
    future_ids: tuple[FutureId, ...]


@dataclass(frozen=True)
class TaskWaitingUntilTime:
    """Task is waiting until a specific time."""

    task_id: TaskId
    target_time: float  # Unix timestamp


@dataclass(frozen=True)
class TaskWaitingForDuration:
    """Task is waiting for a duration."""

    task_id: TaskId
    seconds: float


# ============================================================================
# Task Creation Events
# ============================================================================


@dataclass(frozen=True)
class TaskCreated:
    """A new task was created.

    Runtime should add this task to its scheduling queue.
    """

    task_id: TaskId
    future_id: FutureId  # Future that will hold the task's result
    program: Program
    env: Environment
    store: Store | None = None  # Initial store (or None to share parent's)


@dataclass(frozen=True)
class TasksCreated:
    """Multiple tasks were created (for gather/race).

    All tasks should be added to scheduling queue.
    """

    tasks: tuple[TaskCreated, ...]


# ============================================================================
# Future Events
# ============================================================================


@dataclass(frozen=True)
class FutureResolved:
    """A future received its value.

    Tasks waiting on this future should be notified.
    """

    future_id: FutureId
    value: Any


@dataclass(frozen=True)
class FutureRejected:
    """A future received an error.

    Tasks waiting on this future should be notified of the failure.
    """

    future_id: FutureId
    error: BaseException
    captured_traceback: CapturedTraceback | None = None


# ============================================================================
# I/O Events
# ============================================================================


@dataclass(frozen=True)
class IORequested:
    """External I/O needs to be executed.

    Runtime executes the operation and resumes the task with result.
    """

    task_id: TaskId
    operation: Any  # The I/O operation


@dataclass(frozen=True)
class AwaitRequested:
    """An awaitable needs to be awaited.

    Runtime awaits the awaitable and resumes the task with result.
    """

    task_id: TaskId
    awaitable: Awaitable[Any]


# ============================================================================
# Scheduling Events
# ============================================================================


@dataclass(frozen=True)
class TaskReady:
    """Task is ready to run.

    Runtime should schedule this task for execution.
    """

    task_id: TaskId


@dataclass(frozen=True)
class TaskYielded:
    """Task yielded control.

    Task stepped but has more work to do. Runtime decides when to continue.
    """

    task_id: TaskId


# ============================================================================
# Type Aliases
# ============================================================================


# Events indicating task state change
TaskStateEvent = TaskDone | TaskFailed | TaskCancelled

# Events indicating task is blocked
BlockingEvent = (
    TaskBlocked
    | TaskWaitingOnFuture
    | TaskWaitingOnFutures
    | TaskRacing
    | TaskWaitingUntilTime
    | TaskWaitingForDuration
)

# Events about task creation
CreationEvent = TaskCreated | TasksCreated

# Events about futures
FutureEvent = FutureResolved | FutureRejected

# Events about I/O
IOEvent = IORequested | AwaitRequested

# Events about scheduling
SchedulingEvent = TaskReady | TaskYielded

# All event types
Event = TaskStateEvent | BlockingEvent | CreationEvent | FutureEvent | IOEvent | SchedulingEvent


__all__ = [
    # Task lifecycle
    "TaskDone",
    "TaskFailed",
    "TaskCancelled",
    # Task blocking
    "TaskBlocked",
    "TaskWaitingOnFuture",
    "TaskWaitingOnFutures",
    "TaskRacing",
    "TaskWaitingUntilTime",
    "TaskWaitingForDuration",
    # Task creation
    "TaskCreated",
    "TasksCreated",
    # Future events
    "FutureResolved",
    "FutureRejected",
    # I/O events
    "IORequested",
    "AwaitRequested",
    # Scheduling events
    "TaskReady",
    "TaskYielded",
    # Type aliases
    "TaskStateEvent",
    "BlockingEvent",
    "CreationEvent",
    "FutureEvent",
    "IOEvent",
    "SchedulingEvent",
    "Event",
]
