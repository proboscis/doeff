"""CESK machine control and state types.

This module provides:
- Control types: Value, Error, EffectControl, ProgramControl
- TaskState: Per-task state (C, E, K)
- TaskStatus: Running, Blocked, Waiting, Done, Failed
- Condition: WaitingOn, GatherCondition, RaceCondition
- CESKState: Full multi-task machine state (all tasks + shared store)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, TypeAlias

from doeff._vendor import FrozenDict
from doeff._types_internal import EffectBase
from doeff.cesk.types import Environment, FutureId, Store, TaskId
from doeff.cesk.frames import Kontinuation

if TYPE_CHECKING:
    from doeff.cesk_traceback import CapturedTraceback
    from doeff.program import Program


# ============================================================================
# Control Types
# ============================================================================


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

    program: Program


Control: TypeAlias = Value | Error | EffectControl | ProgramControl


# ============================================================================
# Task Status
# ============================================================================


class TaskStatus(Enum):
    """Status of a task in the CESK machine."""

    RUNNING = auto()
    """Task is ready to run or currently executing."""

    BLOCKED = auto()
    """Task is waiting for external I/O (runtime handles this)."""

    WAITING = auto()
    """Task is waiting on a condition (future, time, gather, race)."""

    DONE = auto()
    """Task completed successfully with a value."""

    FAILED = auto()
    """Task failed with an exception."""

    CANCELLED = auto()
    """Task was cancelled (e.g., by race)."""


# ============================================================================
# Conditions (What a task is waiting on)
# ============================================================================


@dataclass(frozen=True)
class WaitingOn:
    """Task is waiting for a single future to complete."""

    future_id: FutureId


@dataclass(frozen=True)
class GatherCondition:
    """Task is waiting for multiple futures to complete (gather)."""

    future_ids: tuple[FutureId, ...]
    completed: frozenset[FutureId] = field(default_factory=frozenset)


@dataclass(frozen=True)
class RaceCondition:
    """Task is waiting for first of multiple futures to complete (race)."""

    future_ids: tuple[FutureId, ...]


@dataclass(frozen=True)
class TimeCondition:
    """Task is waiting until a specific time."""

    target_time: float  # Unix timestamp


Condition: TypeAlias = WaitingOn | GatherCondition | RaceCondition | TimeCondition


# ============================================================================
# Per-Task State
# ============================================================================


@dataclass
class TaskState:
    """State of a single task in the CESK machine.

    Each task has:
    - C: Control - current computation state
    - E: Environment - variable bindings (immutable)
    - K: Kontinuation - continuation stack

    Note: Store (S) is shared across all tasks and held in CESKState.
    """

    task_id: TaskId
    C: Control
    E: Environment
    K: Kontinuation
    status: TaskStatus = TaskStatus.RUNNING
    condition: Condition | None = None
    future_id: FutureId | None = None  # Future that will hold this task's result

    def with_control(self, c: Control) -> TaskState:
        """Return new TaskState with updated control."""
        return TaskState(
            task_id=self.task_id,
            C=c,
            E=self.E,
            K=self.K,
            status=self.status,
            condition=self.condition,
            future_id=self.future_id,
        )

    def with_environment(self, e: Environment) -> TaskState:
        """Return new TaskState with updated environment."""
        return TaskState(
            task_id=self.task_id,
            C=self.C,
            E=e,
            K=self.K,
            status=self.status,
            condition=self.condition,
            future_id=self.future_id,
        )

    def with_kontinuation(self, k: Kontinuation) -> TaskState:
        """Return new TaskState with updated kontinuation."""
        return TaskState(
            task_id=self.task_id,
            C=self.C,
            E=self.E,
            K=k,
            status=self.status,
            condition=self.condition,
            future_id=self.future_id,
        )

    def with_status(
        self, status: TaskStatus, condition: Condition | None = None
    ) -> TaskState:
        """Return new TaskState with updated status and optional condition."""
        return TaskState(
            task_id=self.task_id,
            C=self.C,
            E=self.E,
            K=self.K,
            status=status,
            condition=condition,
            future_id=self.future_id,
        )


# ============================================================================
# Future State
# ============================================================================


@dataclass(frozen=True)
class FutureState:
    """State of a future (pending result)."""

    future_id: FutureId
    producer_task: TaskId
    value: Any | None = None  # Result value when done
    error: BaseException | None = None  # Error when failed
    is_done: bool = False
    waiters: frozenset[TaskId] = field(default_factory=frozenset)

    def with_value(self, value: Any) -> FutureState:
        """Return new FutureState with result value."""
        return FutureState(
            future_id=self.future_id,
            producer_task=self.producer_task,
            value=value,
            error=None,
            is_done=True,
            waiters=self.waiters,
        )

    def with_error(self, error: BaseException) -> FutureState:
        """Return new FutureState with error."""
        return FutureState(
            future_id=self.future_id,
            producer_task=self.producer_task,
            value=None,
            error=error,
            is_done=True,
            waiters=self.waiters,
        )

    def with_waiter(self, task_id: TaskId) -> FutureState:
        """Return new FutureState with added waiter."""
        return FutureState(
            future_id=self.future_id,
            producer_task=self.producer_task,
            value=self.value,
            error=self.error,
            is_done=self.is_done,
            waiters=self.waiters | {task_id},
        )


# ============================================================================
# Multi-Task CESK State
# ============================================================================


@dataclass
class CESKState:
    """Full multi-task CESK machine state.

    This represents the complete computation state:
    - tasks: All tasks indexed by TaskId
    - futures: All futures indexed by FutureId
    - S: Shared store across all tasks
    - next_task_id: Counter for generating unique TaskIds
    - next_future_id: Counter for generating unique FutureIds

    For single-task compatibility, use CESKState.initial() which creates
    a state with one root task.
    """

    tasks: dict[TaskId, TaskState]
    futures: dict[FutureId, FutureState]
    S: Store
    next_task_id: int = 1
    next_future_id: int = 1

    # === Backwards Compatibility Properties ===
    # These provide access patterns matching the single-task API

    @property
    def C(self) -> Control:
        """Get control of root task (backwards compatibility)."""
        root = TaskId(0)
        if root in self.tasks:
            return self.tasks[root].C
        # Fallback to first task if root doesn't exist
        if self.tasks:
            return next(iter(self.tasks.values())).C
        raise ValueError("No tasks in CESKState")

    @property
    def E(self) -> Environment:
        """Get environment of root task (backwards compatibility)."""
        root = TaskId(0)
        if root in self.tasks:
            return self.tasks[root].E
        if self.tasks:
            return next(iter(self.tasks.values())).E
        raise ValueError("No tasks in CESKState")

    @property
    def K(self) -> Kontinuation:
        """Get kontinuation of root task (backwards compatibility)."""
        root = TaskId(0)
        if root in self.tasks:
            return self.tasks[root].K
        if self.tasks:
            return next(iter(self.tasks.values())).K
        raise ValueError("No tasks in CESKState")

    # === Factory Methods ===

    @classmethod
    def initial(
        cls,
        program: Program,
        env: Environment | dict[Any, Any] | None = None,
        store: Store | None = None,
    ) -> CESKState:
        """Create initial state with a single root task."""
        if env is None:
            env_frozen = FrozenDict()
        elif isinstance(env, FrozenDict):
            env_frozen = env
        else:
            env_frozen = FrozenDict(env)

        root_task_id = TaskId(0)
        root_task = TaskState(
            task_id=root_task_id,
            C=ProgramControl(program),
            E=env_frozen,
            K=[],
            status=TaskStatus.RUNNING,
        )

        return cls(
            tasks={root_task_id: root_task},
            futures={},
            S=store if store is not None else {},
            next_task_id=1,
            next_future_id=0,
        )

    @classmethod
    def from_single_task(
        cls,
        C: Control,
        E: Environment,
        S: Store,
        K: Kontinuation,
    ) -> CESKState:
        """Create state from single-task components (backwards compatibility)."""
        root_task_id = TaskId(0)
        root_task = TaskState(
            task_id=root_task_id,
            C=C,
            E=E,
            K=K,
            status=TaskStatus.RUNNING,
        )
        return cls(
            tasks={root_task_id: root_task},
            futures={},
            S=S,
            next_task_id=1,
            next_future_id=0,
        )

    # === Task Management ===

    def allocate_task_id(self) -> tuple[CESKState, TaskId]:
        """Allocate a new task ID, returning updated state and the ID."""
        new_id = TaskId(self.next_task_id)
        new_state = CESKState(
            tasks=self.tasks,
            futures=self.futures,
            S=self.S,
            next_task_id=self.next_task_id + 1,
            next_future_id=self.next_future_id,
        )
        return new_state, new_id

    def allocate_future_id(self) -> tuple[CESKState, FutureId]:
        """Allocate a new future ID, returning updated state and the ID."""
        new_id = FutureId(self.next_future_id)
        new_state = CESKState(
            tasks=self.tasks,
            futures=self.futures,
            S=self.S,
            next_task_id=self.next_task_id,
            next_future_id=self.next_future_id + 1,
        )
        return new_state, new_id

    def add_task(self, task: TaskState) -> CESKState:
        """Add a task to the state."""
        new_tasks = dict(self.tasks)
        new_tasks[task.task_id] = task
        return CESKState(
            tasks=new_tasks,
            futures=self.futures,
            S=self.S,
            next_task_id=self.next_task_id,
            next_future_id=self.next_future_id,
        )

    def update_task(self, task: TaskState) -> CESKState:
        """Update an existing task."""
        new_tasks = dict(self.tasks)
        new_tasks[task.task_id] = task
        return CESKState(
            tasks=new_tasks,
            futures=self.futures,
            S=self.S,
            next_task_id=self.next_task_id,
            next_future_id=self.next_future_id,
        )

    def add_future(self, future: FutureState) -> CESKState:
        """Add a future to the state."""
        new_futures = dict(self.futures)
        new_futures[future.future_id] = future
        return CESKState(
            tasks=self.tasks,
            futures=new_futures,
            S=self.S,
            next_task_id=self.next_task_id,
            next_future_id=self.next_future_id,
        )

    def update_future(self, future: FutureState) -> CESKState:
        """Update an existing future."""
        new_futures = dict(self.futures)
        new_futures[future.future_id] = future
        return CESKState(
            tasks=self.tasks,
            futures=new_futures,
            S=self.S,
            next_task_id=self.next_task_id,
            next_future_id=self.next_future_id,
        )

    def with_store(self, store: Store) -> CESKState:
        """Return new state with updated store."""
        return CESKState(
            tasks=self.tasks,
            futures=self.futures,
            S=store,
            next_task_id=self.next_task_id,
            next_future_id=self.next_future_id,
        )

    # === Query Methods ===

    def get_runnable_tasks(self) -> list[TaskId]:
        """Get list of tasks that are ready to run."""
        return [
            task_id
            for task_id, task in self.tasks.items()
            if task.status == TaskStatus.RUNNING
        ]

    def get_waiting_tasks(self) -> list[TaskId]:
        """Get list of tasks that are waiting on conditions."""
        return [
            task_id
            for task_id, task in self.tasks.items()
            if task.status == TaskStatus.WAITING
        ]

    def is_all_done(self) -> bool:
        """Check if all tasks are in terminal states."""
        return all(
            task.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)
            for task in self.tasks.values()
        )


__all__ = [
    # Control types
    "Value",
    "Error",
    "EffectControl",
    "ProgramControl",
    "Control",
    # Task status
    "TaskStatus",
    # Conditions
    "WaitingOn",
    "GatherCondition",
    "RaceCondition",
    "TimeCondition",
    "Condition",
    # Task state
    "TaskState",
    "FutureState",
    # CESK state
    "CESKState",
]
