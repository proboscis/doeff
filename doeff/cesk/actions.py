from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from doeff.cesk.types import TaskId, FutureId, Environment

if TYPE_CHECKING:
    from doeff.program import Program
    from collections.abc import Awaitable


@dataclass(frozen=True)
class RunProgram:
    program: Program
    env: Environment | None = None


@dataclass(frozen=True)
class CreateTask:
    task_id: TaskId
    program: Program
    env: Environment | None = None
    parent_task_id: TaskId | None = None


@dataclass(frozen=True)
class CreateTasks:
    task_specs: list[tuple[TaskId, Program, Environment | None]]
    parent_task_id: TaskId | None = None


@dataclass(frozen=True)
class CancelTasks:
    task_ids: list[TaskId]


@dataclass(frozen=True)
class PerformIO:
    io_function: Any
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] | None = None


@dataclass(frozen=True)
class AwaitExternal:
    awaitable: Awaitable[Any]
    future_id: FutureId


@dataclass(frozen=True)
class ScheduleAt:
    target_time: datetime
    task_id: TaskId


@dataclass(frozen=True)
class GetCurrentTime:
    pass


Action = (
    RunProgram
    | CreateTask
    | CreateTasks
    | CancelTasks
    | PerformIO
    | AwaitExternal
    | ScheduleAt
    | GetCurrentTime
)


__all__ = [
    "RunProgram",
    "CreateTask",
    "CreateTasks",
    "CancelTasks",
    "PerformIO",
    "AwaitExternal",
    "ScheduleAt",
    "GetCurrentTime",
    "Action",
]
