"""CESK machine control and state types for unified multi-task architecture.

This module provides the core state types for the unified CESK machine:
- Control: The current control state (Value, Error, EffectControl, ProgramControl)
- TaskStatus: The status of a task (Ready, Blocked, Requesting, Done)
- Condition: What a blocked task is waiting for
- Request: Operations that require runtime intervention
- TaskState: Per-task CESK state
- CESKState: Multi-task unified state
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeAlias

from doeff._types_internal import EffectBase
from doeff._vendor import FrozenDict, Result
from doeff.cesk.types import (
    Environment,
    FutureId,
    SpawnId,
    Store,
    TaskId,
)

if TYPE_CHECKING:
    from doeff.cesk.frames import Kontinuation
    from doeff.cesk_traceback import CapturedTraceback
    from doeff.effects._program_types import ProgramLike
    from doeff.effects.spawn import SpawnBackend


# ============================================
# Control States
# ============================================

@dataclass(frozen=True)
class Value:
    """Control state: computation has produced a value."""

    v: Any


@dataclass(frozen=True)
class Error:
    """Control state: computation has raised an exception."""

    ex: BaseException
    captured_traceback: CapturedTraceback | None = None


@dataclass(frozen=True)
class EffectControl:
    """Control state: need to handle an effect."""

    effect: EffectBase


@dataclass(frozen=True)
class ProgramControl:
    """Control state: need to execute a program."""

    program: ProgramLike


Control: TypeAlias = Value | Error | EffectControl | ProgramControl


# ============================================
# Conditions (what to wait for)
# ============================================

@dataclass(frozen=True)
class TimeCondition:
    """Task is waiting until a specific time."""

    wake_time: datetime


@dataclass(frozen=True)
class FutureCondition:
    """Task is waiting for a future to be resolved."""

    future_id: FutureId


@dataclass(frozen=True)
class TaskCondition:
    """Task is waiting for another task to complete."""

    task_id: TaskId


@dataclass(frozen=True)
class SpawnCondition:
    """Task is waiting for a spawned process to complete."""

    spawn_id: SpawnId


Condition: TypeAlias = TimeCondition | FutureCondition | TaskCondition | SpawnCondition


# ============================================
# Requests (runtime operations)
# ============================================

@dataclass(frozen=True)
class CreateTask:
    """Request to create a new task from a program."""

    program: ProgramLike


@dataclass(frozen=True)
class CreateFuture:
    """Request to create a new unresolved future."""



@dataclass(frozen=True)
class ResolveFuture:
    """Request to resolve a future with a value."""

    future_id: FutureId
    value: Any


@dataclass(frozen=True)
class PerformIO:
    """Request to perform a side-effectful IO action."""

    action: Callable[[], Any]


@dataclass(frozen=True)
class AwaitExternal:
    """Request to await an external awaitable (asyncio integration)."""

    awaitable: Awaitable[Any]


@dataclass(frozen=True)
class CreateSpawn:
    """Request to spawn a program on an external backend."""

    program: ProgramLike
    backend: SpawnBackend


Request: TypeAlias = (
    CreateTask
    | CreateFuture
    | ResolveFuture
    | PerformIO
    | AwaitExternal
    | CreateSpawn
)


# ============================================
# Task Status
# ============================================

@dataclass(frozen=True)
class Ready:
    """Task is ready to run, with a value to resume with."""

    resume_value: Any = None


@dataclass(frozen=True)
class Blocked:
    """Task is waiting for a condition to be satisfied."""

    condition: Condition


@dataclass(frozen=True)
class Requesting:
    """Task needs the runtime to perform an operation."""

    request: Request


@dataclass(frozen=True)
class Done:
    """Task has completed with a result."""

    result: Result[Any]

    @classmethod
    def ok(cls, value: Any) -> Done:
        """Create a successful Done status."""
        from doeff._vendor import Ok
        return cls(Ok(value))

    @classmethod
    def err(cls, error: Exception) -> Done:
        """Create a failed Done status."""
        from doeff._vendor import Err
        return cls(Err(error))


TaskStatus: TypeAlias = Ready | Blocked | Requesting | Done


# ============================================
# Task State (per-task CESK)
# ============================================

@dataclass
class TaskState:
    """Per-task CESK state.

    Each task has:
    - C: Control (current computation state)
    - E: Environment (immutable reader context)
    - K: Kontinuation (call stack)
    - status: Current task status

    Note: Store (S) is shared across all tasks and stored in CESKState.
    """

    control: Control
    env: Environment
    kontinuation: Kontinuation
    status: TaskStatus

    @classmethod
    def initial(
        cls,
        program: ProgramLike,
        env: Environment | dict[Any, Any] | None = None,
    ) -> TaskState:
        """Create initial state for a program."""
        if env is None:
            env_frozen = FrozenDict()
        elif isinstance(env, FrozenDict):
            env_frozen = env
        else:
            env_frozen = FrozenDict(env)

        return cls(
            control=ProgramControl(program),
            env=env_frozen,
            kontinuation=[],
            status=Ready(),
        )

    def with_control(self, control: Control) -> TaskState:
        """Return a new TaskState with updated control."""
        return TaskState(
            control=control,
            env=self.env,
            kontinuation=self.kontinuation,
            status=self.status,
        )

    def with_env(self, env: Environment) -> TaskState:
        """Return a new TaskState with updated environment."""
        return TaskState(
            control=self.control,
            env=env,
            kontinuation=self.kontinuation,
            status=self.status,
        )

    def with_kontinuation(self, kontinuation: Kontinuation) -> TaskState:
        """Return a new TaskState with updated kontinuation."""
        return TaskState(
            control=self.control,
            env=self.env,
            kontinuation=kontinuation,
            status=self.status,
        )

    def with_status(self, status: TaskStatus) -> TaskState:
        """Return a new TaskState with updated status."""
        return TaskState(
            control=self.control,
            env=self.env,
            kontinuation=self.kontinuation,
            status=status,
        )

    def resume_with(self, value: Any) -> TaskState:
        """Resume this task with a value, setting control to Value and status to Ready."""
        return TaskState(
            control=Value(value),
            env=self.env,
            kontinuation=self.kontinuation,
            status=Ready(value),
        )

    def fail_with(self, error: BaseException, captured_traceback: CapturedTraceback | None = None) -> TaskState:
        """Fail this task with an error."""
        return TaskState(
            control=Error(error, captured_traceback),
            env=self.env,
            kontinuation=self.kontinuation,
            status=Ready(),  # Ready to process the error through K
        )


# ============================================
# Multi-Task CESK State
# ============================================

class CESKState:
    """Unified multi-task CESK state.

    Contains all tasks and shared state:
    - tasks: Mapping from TaskId to TaskState
    - store: Shared mutable store (S component)
    - main_task: The ID of the main (root) task
    - futures: Resolved future values
    - spawn_results: Results from spawned processes

    Supports both new interface (tasks, store, main_task) and legacy
    interface (C, E, S, K) for backward compatibility.
    """

    def __init__(
        self,
        # New interface
        tasks: dict[TaskId, TaskState] | None = None,
        store: Store | None = None,
        main_task: TaskId | None = None,
        futures: dict[FutureId, Any] | None = None,
        spawn_results: dict[SpawnId, Any] | None = None,
        # Legacy interface
        C: Control | None = None,
        E: Environment | None = None,
        S: Store | None = None,
        K: Kontinuation | None = None,
    ):
        """Initialize CESKState with either new or legacy interface.

        New interface:
            CESKState(tasks={...}, store={...}, main_task=TaskId(...))

        Legacy interface (for backward compatibility):
            CESKState(C=Value(42), E=FrozenDict(), S={}, K=[])
        """
        if tasks is not None:
            # New interface
            self.tasks = tasks
            self.store = store if store is not None else {}
            self.main_task = main_task if main_task is not None else next(iter(tasks.keys()))
            self.futures = futures if futures is not None else {}
            self.spawn_results = spawn_results if spawn_results is not None else {}
        elif C is not None:
            # Legacy interface - create a single-task state
            task_id = TaskId.new()
            task_state = TaskState(
                control=C,
                env=E if E is not None else FrozenDict(),
                kontinuation=K if K is not None else [],
                status=Ready(),
            )
            self.tasks = {task_id: task_state}
            self.store = S if S is not None else {}
            self.main_task = task_id
            self.futures = {}
            self.spawn_results = {}
        else:
            raise ValueError("CESKState requires either 'tasks' or 'C' to be provided")

    # Legacy single-task interface properties
    @property
    def C(self) -> Control:
        """Control of main task (legacy interface)."""
        return self.tasks[self.main_task].control

    @property
    def E(self) -> Environment:
        """Environment of main task (legacy interface)."""
        return self.tasks[self.main_task].env

    @property
    def S(self) -> Store:
        """Shared store (legacy interface)."""
        return self.store

    @property
    def K(self) -> Kontinuation:
        """Kontinuation of main task (legacy interface)."""
        return self.tasks[self.main_task].kontinuation

    def __repr__(self) -> str:
        return f"CESKState(tasks={self.tasks!r}, store={self.store!r}, main_task={self.main_task!r}, futures={self.futures!r}, spawn_results={self.spawn_results!r})"

    @classmethod
    def initial(
        cls,
        program: ProgramLike,
        env: Environment | dict[Any, Any] | None = None,
        store: Store | None = None,
    ) -> CESKState:
        """Create initial state for a program.

        Creates a single main task running the given program.
        """
        main_task = TaskId.new()
        task_state = TaskState.initial(program, env)

        return cls(
            tasks={main_task: task_state},
            store=store if store is not None else {},
            main_task=main_task,
        )

    def get_task(self, task_id: TaskId) -> TaskState | None:
        """Get the state of a specific task."""
        return self.tasks.get(task_id)

    def with_task(self, task_id: TaskId, task_state: TaskState) -> CESKState:
        """Return a new CESKState with updated task state."""
        new_tasks = dict(self.tasks)
        new_tasks[task_id] = task_state
        return CESKState(
            tasks=new_tasks,
            store=self.store,
            main_task=self.main_task,
            futures=self.futures,
            spawn_results=self.spawn_results,
        )

    def add_task(self, task_id: TaskId, task_state: TaskState) -> CESKState:
        """Add a new task to the state."""
        if task_id in self.tasks:
            raise ValueError(f"Task {task_id} already exists")
        new_tasks = dict(self.tasks)
        new_tasks[task_id] = task_state
        return CESKState(
            tasks=new_tasks,
            store=self.store,
            main_task=self.main_task,
            futures=self.futures,
            spawn_results=self.spawn_results,
        )

    def remove_task(self, task_id: TaskId) -> CESKState:
        """Remove a task from the state."""
        if task_id == self.main_task:
            raise ValueError("Cannot remove main task")
        new_tasks = dict(self.tasks)
        del new_tasks[task_id]
        return CESKState(
            tasks=new_tasks,
            store=self.store,
            main_task=self.main_task,
            futures=self.futures,
            spawn_results=self.spawn_results,
        )

    def with_future(self, future_id: FutureId, value: Any) -> CESKState:
        """Return a new CESKState with a resolved future."""
        new_futures = dict(self.futures)
        new_futures[future_id] = value
        return CESKState(
            tasks=self.tasks,
            store=self.store,
            main_task=self.main_task,
            futures=new_futures,
            spawn_results=self.spawn_results,
        )

    def get_future(self, future_id: FutureId) -> Any | None:
        """Get the value of a resolved future, or None if not resolved."""
        return self.futures.get(future_id)

    def is_future_resolved(self, future_id: FutureId) -> bool:
        """Check if a future has been resolved."""
        return future_id in self.futures

    def with_spawn_result(self, spawn_id: SpawnId, value: Any) -> CESKState:
        """Return a new CESKState with a spawn result."""
        new_spawn_results = dict(self.spawn_results)
        new_spawn_results[spawn_id] = value
        return CESKState(
            tasks=self.tasks,
            store=self.store,
            main_task=self.main_task,
            futures=self.futures,
            spawn_results=new_spawn_results,
        )

    def get_ready_tasks(self) -> list[TaskId]:
        """Get all tasks that are ready to run."""
        return [
            task_id
            for task_id, task in self.tasks.items()
            if isinstance(task.status, Ready)
        ]

    def is_main_task_done(self) -> bool:
        """Check if the main task has completed."""
        main = self.tasks.get(self.main_task)
        return main is not None and isinstance(main.status, Done)

    def get_main_result(self) -> Result[Any] | None:
        """Get the result of the main task if it's done."""
        main = self.tasks.get(self.main_task)
        if main is not None and isinstance(main.status, Done):
            return main.status.result
        return None


__all__ = [
    "AwaitExternal",
    "Blocked",
    "CESKState",
    "Condition",
    "Control",
    "CreateFuture",
    "CreateSpawn",
    # Requests
    "CreateTask",
    "Done",
    "EffectControl",
    "Error",
    "FutureCondition",
    "PerformIO",
    "ProgramControl",
    # Task status
    "Ready",
    "Request",
    "Requesting",
    "ResolveFuture",
    "SpawnCondition",
    "TaskCondition",
    # State classes
    "TaskState",
    "TaskStatus",
    # Conditions
    "TimeCondition",
    # Control states
    "Value",
]
