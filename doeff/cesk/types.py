"""CESK Machine type definitions for the unified multi-task architecture."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, NewType, TypeAlias

from doeff._vendor import FrozenDict

if TYPE_CHECKING:
    from doeff.cesk_traceback import CapturedTraceback


TaskId = NewType("TaskId", int)
FutureId = NewType("FutureId", int)
SpawnId = NewType("SpawnId", int)

Environment: TypeAlias = FrozenDict[Any, Any]
Store: TypeAlias = dict[str, Any]


@dataclass(frozen=True)
class TaskOk:
    value: Any
    task_id: TaskId


@dataclass(frozen=True)
class TaskErr:
    error: BaseException
    task_id: TaskId
    captured_traceback: CapturedTraceback | None = None


TaskResult: TypeAlias = TaskOk | TaskErr


@dataclass(frozen=True)
class SimulatedTime:
    value: datetime
    
    @classmethod
    def now(cls) -> SimulatedTime:
        return cls(datetime.now())
    
    def __lt__(self, other: SimulatedTime) -> bool:
        return self.value < other.value
    
    def __le__(self, other: SimulatedTime) -> bool:
        return self.value <= other.value


@dataclass
class IdGenerator:
    _next_task_id: int = field(default=0, init=False)
    _next_future_id: int = field(default=0, init=False)
    _next_spawn_id: int = field(default=0, init=False)
    
    def next_task_id(self) -> TaskId:
        tid = TaskId(self._next_task_id)
        self._next_task_id += 1
        return tid
    
    def next_future_id(self) -> FutureId:
        fid = FutureId(self._next_future_id)
        self._next_future_id += 1
        return fid
    
    def next_spawn_id(self) -> SpawnId:
        sid = SpawnId(self._next_spawn_id)
        self._next_spawn_id += 1
        return sid


__all__ = [
    "TaskId",
    "FutureId",
    "SpawnId",
    "Environment",
    "Store",
    "TaskOk",
    "TaskErr",
    "TaskResult",
    "SimulatedTime",
    "IdGenerator",
]
