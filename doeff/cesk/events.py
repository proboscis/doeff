"""Event types emitted by step function to the Runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeAlias

from doeff.cesk.types import FutureId, Store, TaskId

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk.state import CESKState


@dataclass(frozen=True)
class TaskCompleted:
    task_id: TaskId
    value: Any
    state: CESKState


@dataclass(frozen=True)
class TaskFailed:
    task_id: TaskId
    error: BaseException
    state: CESKState


@dataclass(frozen=True)
class TaskBlocked:
    task_id: TaskId
    state: CESKState


@dataclass(frozen=True)
class EffectSuspended:
    task_id: TaskId
    effect: EffectBase
    state: CESKState


@dataclass(frozen=True)
class IORequested:
    task_id: TaskId
    io_callable: Any
    io_id: int
    state: CESKState


@dataclass(frozen=True)
class ExternalAwait:
    task_id: TaskId
    awaitable: Any
    future_id: FutureId
    state: CESKState


@dataclass(frozen=True)
class TimeWait:
    task_id: TaskId
    target: datetime
    state: CESKState


@dataclass(frozen=True)
class TasksCreated:
    parent_id: TaskId
    child_ids: tuple[TaskId, ...]
    state: CESKState


@dataclass(frozen=True)
class AllTasksComplete:
    state: CESKState


@dataclass(frozen=True)
class NeedsTimeAdvance:
    earliest_wake: datetime
    state: CESKState


@dataclass(frozen=True)
class Stepped:
    state: CESKState


Event: TypeAlias = (
    TaskCompleted
    | TaskFailed
    | TaskBlocked
    | EffectSuspended
    | IORequested
    | ExternalAwait
    | TimeWait
    | TasksCreated
    | AllTasksComplete
    | NeedsTimeAdvance
    | Stepped
)


__all__ = [
    "TaskCompleted",
    "TaskFailed",
    "TaskBlocked",
    "EffectSuspended",
    "IORequested",
    "ExternalAwait",
    "TimeWait",
    "TasksCreated",
    "AllTasksComplete",
    "NeedsTimeAdvance",
    "Stepped",
    "Event",
]
