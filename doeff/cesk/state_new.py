from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, TypeAlias

from doeff._vendor import FrozenDict
from doeff._types_internal import EffectBase
from doeff.cesk.types import Environment, Store, TaskId, FutureId

if TYPE_CHECKING:
    from doeff.program import Program
    from doeff.cesk_traceback import CapturedTraceback


class TaskStatus(Enum):
    RUNNING = auto()
    BLOCKED = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass(frozen=True)
class Value:
    v: Any


@dataclass(frozen=True)
class Error:
    ex: BaseException
    captured_traceback: CapturedTraceback | None = None


@dataclass(frozen=True)
class EffectControl:
    effect: EffectBase


@dataclass(frozen=True)
class ProgramControl:
    program: Program


Control: TypeAlias = Value | Error | EffectControl | ProgramControl


@dataclass(frozen=True)
class WaitingForFuture:
    future_id: FutureId


@dataclass(frozen=True)
class WaitingForTime:
    target_time: datetime


@dataclass(frozen=True)
class GatherCondition:
    child_task_ids: tuple[TaskId, ...]
    results: tuple[Any, ...]


@dataclass(frozen=True)
class RaceCondition:
    child_task_ids: tuple[TaskId, ...]


Condition: TypeAlias = WaitingForFuture | WaitingForTime | GatherCondition | RaceCondition


@dataclass(frozen=True)
class TaskState:
    task_id: TaskId
    control: Control
    environment: Environment
    kontinuation: list[Any]
    status: TaskStatus = TaskStatus.RUNNING
    condition: Condition | None = None
    parent_task_id: TaskId | None = None


@dataclass(frozen=True)
class CESKState:
    tasks: dict[TaskId, TaskState]
    store: Store
    active_task_id: TaskId | None = None
    futures: dict[FutureId, Any] = field(default_factory=dict)
    
    @classmethod
    def initial(
        cls,
        program: Program,
        task_id: TaskId,
        env: Environment | dict[Any, Any] | None = None,
        store: Store | None = None,
    ) -> CESKState:
        if env is None:
            env_frozen = FrozenDict()
        elif isinstance(env, FrozenDict):
            env_frozen = env
        else:
            env_frozen = FrozenDict(env)
        
        initial_task = TaskState(
            task_id=task_id,
            control=ProgramControl(program),
            environment=env_frozen,
            kontinuation=[],
            status=TaskStatus.RUNNING,
        )
        
        return cls(
            tasks={task_id: initial_task},
            store=store if store is not None else {},
            active_task_id=task_id,
            futures={},
        )
    
    def with_task(self, task_id: TaskId, task_state: TaskState) -> CESKState:
        new_tasks = self.tasks.copy()
        new_tasks[task_id] = task_state
        return CESKState(
            tasks=new_tasks,
            store=self.store,
            active_task_id=self.active_task_id,
            futures=self.futures,
        )
    
    def with_active_task(self, task_id: TaskId | None) -> CESKState:
        return CESKState(
            tasks=self.tasks,
            store=self.store,
            active_task_id=task_id,
            futures=self.futures,
        )
    
    def with_store(self, store: Store) -> CESKState:
        return CESKState(
            tasks=self.tasks,
            store=store,
            active_task_id=self.active_task_id,
            futures=self.futures,
        )
    
    def with_future(self, future_id: FutureId, value: Any) -> CESKState:
        new_futures = self.futures.copy()
        new_futures[future_id] = value
        return CESKState(
            tasks=self.tasks,
            store=self.store,
            active_task_id=self.active_task_id,
            futures=new_futures,
        )
    
    def get_active_task(self) -> TaskState | None:
        if self.active_task_id is None:
            return None
        return self.tasks.get(self.active_task_id)
    
    def remove_task(self, task_id: TaskId) -> CESKState:
        new_tasks = self.tasks.copy()
        if task_id in new_tasks:
            del new_tasks[task_id]
        
        new_active = self.active_task_id
        if new_active == task_id:
            new_active = None
        
        return CESKState(
            tasks=new_tasks,
            store=self.store,
            active_task_id=new_active,
            futures=self.futures,
        )


__all__ = [
    "TaskStatus",
    "Value",
    "Error",
    "EffectControl",
    "ProgramControl",
    "Control",
    "WaitingForFuture",
    "WaitingForTime",
    "GatherCondition",
    "RaceCondition",
    "Condition",
    "TaskState",
    "CESKState",
]
