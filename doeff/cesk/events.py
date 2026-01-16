from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from doeff.cesk.types import TaskId, FutureId


@dataclass(frozen=True)
class TaskCompleted:
    task_id: TaskId
    result: Any


@dataclass(frozen=True)
class TaskFailed:
    task_id: TaskId
    error: BaseException


@dataclass(frozen=True)
class TaskCancelled:
    task_id: TaskId


@dataclass(frozen=True)
class FutureResolved:
    future_id: FutureId
    value: Any


@dataclass(frozen=True)
class FutureRejected:
    future_id: FutureId
    error: BaseException


@dataclass(frozen=True)
class TimeAdvanced:
    current_time: datetime


@dataclass(frozen=True)
class IOCompleted:
    result: Any


@dataclass(frozen=True)
class IOFailed:
    error: BaseException


Event = (
    TaskCompleted
    | TaskFailed
    | TaskCancelled
    | FutureResolved
    | FutureRejected
    | TimeAdvanced
    | IOCompleted
    | IOFailed
)


__all__ = [
    "TaskCompleted",
    "TaskFailed",
    "TaskCancelled",
    "FutureResolved",
    "FutureRejected",
    "TimeAdvanced",
    "IOCompleted",
    "IOFailed",
    "Event",
]
