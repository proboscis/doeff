"""Action types for the CESK machine.

Actions are what handlers return to tell step() what to do.
This follows the pattern: Handler → Action → step() → Event → Runtime

Action types:
- Resume: Resume computation with a value
- ResumeError: Resume computation with an error
- RunProgram: Execute a sub-program
- CreateTask: Spawn a new task
- CreateTasks: Spawn multiple tasks (for gather/race)
- WaitOnFuture: Wait for a future to complete
- WaitOnFutures: Wait for multiple futures (gather)
- RaceOnFutures: Wait for first of multiple futures (race)
- WaitUntilTime: Wait until a specific time
- PerformIO: Perform external I/O
- AwaitExternal: Await an external awaitable
- CancelTasks: Cancel running tasks
- ModifyStore: Update the store
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable

from doeff.cesk.types import Environment, FutureId, Store, TaskId

if TYPE_CHECKING:
    from doeff.program import Program


# ============================================================================
# Synchronous Actions (handled immediately by step)
# ============================================================================


@dataclass(frozen=True)
class Resume:
    """Resume computation with a value.

    The most common action - handler computed result synchronously.
    """

    value: Any


@dataclass(frozen=True)
class ResumeError:
    """Resume computation with an error.

    Handler wants to raise an exception in the computation.
    """

    error: BaseException


@dataclass(frozen=True)
class RunProgram:
    """Execute a sub-program.

    Used for control-flow effects that run programs (Local, Intercept, etc).
    Optional env parameter allows running with modified environment.
    """

    program: Program
    env: Environment | None = None  # If None, use current env


@dataclass(frozen=True)
class ModifyStore:
    """Update the store.

    Used for state effects (Get, Put, Modify) and other store updates.
    Can be combined with Resume in HandlerResult.
    """

    updates: dict[str, Any]


# ============================================================================
# Task Management Actions (create/cancel tasks)
# ============================================================================


@dataclass(frozen=True)
class CreateTask:
    """Spawn a new task.

    Creates a new task and future. The parent task can optionally
    wait on the future or continue running.
    """

    program: Program
    env: Environment
    store_snapshot: Store | None = None  # Initial store for child (or share)


@dataclass(frozen=True)
class CreateTasks:
    """Spawn multiple tasks at once (for gather/race).

    Returns multiple future IDs that can be waited on together.
    """

    programs: tuple[Program, ...]
    env: Environment


@dataclass(frozen=True)
class CancelTasks:
    """Cancel running tasks.

    Used by race when first task completes - cancel others.
    """

    task_ids: tuple[TaskId, ...]


# ============================================================================
# Waiting Actions (task suspends until condition met)
# ============================================================================


@dataclass(frozen=True)
class WaitOnFuture:
    """Wait for a single future to complete.

    Task suspends until the future has a value or error.
    """

    future_id: FutureId


@dataclass(frozen=True)
class WaitOnFutures:
    """Wait for multiple futures to complete (gather semantics).

    Task suspends until ALL futures complete. Results collected in order.
    """

    future_ids: tuple[FutureId, ...]


@dataclass(frozen=True)
class RaceOnFutures:
    """Wait for first of multiple futures to complete (race semantics).

    Task suspends until ANY future completes. First result wins.
    Other tasks may be cancelled.
    """

    future_ids: tuple[FutureId, ...]


@dataclass(frozen=True)
class WaitUntilTime:
    """Wait until a specific time.

    Task suspends until the target time is reached.
    """

    target_time: float  # Unix timestamp


@dataclass(frozen=True)
class WaitForDuration:
    """Wait for a duration.

    Task suspends for the specified number of seconds.
    """

    seconds: float


# ============================================================================
# I/O Actions (require runtime execution)
# ============================================================================


@dataclass(frozen=True)
class PerformIO:
    """Perform external I/O.

    Runtime executes the I/O operation and resumes with result.
    The callback is an async function to call.
    """

    operation: Any  # The IO operation (typically a callable)


@dataclass(frozen=True)
class AwaitExternal:
    """Await an external awaitable.

    Runtime awaits the awaitable and resumes with result.
    """

    awaitable: Awaitable[Any]


# ============================================================================
# Compound Actions
# ============================================================================


@dataclass(frozen=True)
class ResumeWithStore:
    """Resume with value and store update.

    Convenience action combining Resume and ModifyStore.
    """

    value: Any
    store: Store


# ============================================================================
# Type Aliases
# ============================================================================


# Actions that can be handled synchronously by step
SyncAction = Resume | ResumeError | RunProgram | ModifyStore | ResumeWithStore

# Actions that create tasks
TaskAction = CreateTask | CreateTasks | CancelTasks

# Actions that cause task to wait
WaitAction = WaitOnFuture | WaitOnFutures | RaceOnFutures | WaitUntilTime | WaitForDuration

# Actions that require runtime
IOAction = PerformIO | AwaitExternal

# All action types
Action = SyncAction | TaskAction | WaitAction | IOAction


__all__ = [
    # Sync actions
    "Resume",
    "ResumeError",
    "RunProgram",
    "ModifyStore",
    "ResumeWithStore",
    # Task actions
    "CreateTask",
    "CreateTasks",
    "CancelTasks",
    # Wait actions
    "WaitOnFuture",
    "WaitOnFutures",
    "RaceOnFutures",
    "WaitUntilTime",
    "WaitForDuration",
    # IO actions
    "PerformIO",
    "AwaitExternal",
    # Type aliases
    "SyncAction",
    "TaskAction",
    "WaitAction",
    "IOAction",
    "Action",
]
