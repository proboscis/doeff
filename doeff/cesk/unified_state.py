"""CESK machine multi-task state types for the unified architecture."""

from __future__ import annotations

from collections.abc import Generator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, TypeAlias

from doeff._vendor import FrozenDict
from doeff._types_internal import EffectBase
from doeff.cesk.types import (
    Environment,
    FutureId,
    IdGenerator,
    SpawnId,
    Store,
    TaskId,
)
from doeff.cesk.state import Control, EffectControl, Error, ProgramControl, Value

if TYPE_CHECKING:
    from doeff.cesk_traceback import CapturedTraceback
    from doeff.cesk.frames import Frame, Kontinuation
    from doeff.program import Program


class TaskStatus(Enum):
    RUNNING = auto()
    BLOCKED = auto()
    COMPLETED = auto()
    FAILED = auto()


@dataclass(frozen=True)
class WaitingForFuture:
    future_id: FutureId


@dataclass(frozen=True)
class WaitingForTime:
    target: datetime


@dataclass(frozen=True)
class WaitingForIO:
    io_id: int


@dataclass(frozen=True)
class WaitingForAny:
    task_ids: frozenset[TaskId]


@dataclass(frozen=True)
class WaitingForAll:
    task_ids: frozenset[TaskId]


Condition: TypeAlias = (
    WaitingForFuture
    | WaitingForTime
    | WaitingForIO
    | WaitingForAny
    | WaitingForAll
)


@dataclass
class TaskState:
    task_id: TaskId
    control: Control
    env: Environment
    kontinuation: list[Frame]
    status: TaskStatus = TaskStatus.RUNNING
    condition: Condition | None = None
    parent_id: TaskId | None = None
    spawn_id: SpawnId | None = None
    
    @classmethod
    def initial(
        cls,
        task_id: TaskId,
        program: Program,
        env: Environment | dict[Any, Any] | None = None,
        parent_id: TaskId | None = None,
        spawn_id: SpawnId | None = None,
    ) -> TaskState:
        if env is None:
            env_frozen = FrozenDict()
        elif isinstance(env, FrozenDict):
            env_frozen = env
        else:
            env_frozen = FrozenDict(env)
        return cls(
            task_id=task_id,
            control=ProgramControl(program),
            env=env_frozen,
            kontinuation=[],
            status=TaskStatus.RUNNING,
            parent_id=parent_id,
            spawn_id=spawn_id,
        )
    
    def with_control(self, control: Control) -> TaskState:
        return TaskState(
            task_id=self.task_id,
            control=control,
            env=self.env,
            kontinuation=self.kontinuation,
            status=self.status,
            condition=self.condition,
            parent_id=self.parent_id,
            spawn_id=self.spawn_id,
        )
    
    def with_status(
        self,
        status: TaskStatus,
        condition: Condition | None = None,
    ) -> TaskState:
        return TaskState(
            task_id=self.task_id,
            control=self.control,
            env=self.env,
            kontinuation=self.kontinuation,
            status=status,
            condition=condition,
            parent_id=self.parent_id,
            spawn_id=self.spawn_id,
        )
    
    def with_env(self, env: Environment) -> TaskState:
        return TaskState(
            task_id=self.task_id,
            control=self.control,
            env=env,
            kontinuation=self.kontinuation,
            status=self.status,
            condition=self.condition,
            parent_id=self.parent_id,
            spawn_id=self.spawn_id,
        )
    
    def push_frame(self, frame: Frame) -> TaskState:
        return TaskState(
            task_id=self.task_id,
            control=self.control,
            env=self.env,
            kontinuation=[frame] + self.kontinuation,
            status=self.status,
            condition=self.condition,
            parent_id=self.parent_id,
            spawn_id=self.spawn_id,
        )
    
    def pop_frame(self) -> tuple[Frame | None, TaskState]:
        if not self.kontinuation:
            return None, self
        frame = self.kontinuation[0]
        new_task = TaskState(
            task_id=self.task_id,
            control=self.control,
            env=self.env,
            kontinuation=self.kontinuation[1:],
            status=self.status,
            condition=self.condition,
            parent_id=self.parent_id,
            spawn_id=self.spawn_id,
        )
        return frame, new_task


@dataclass
class UnifiedCESKState:
    tasks: dict[TaskId, TaskState]
    store: Store
    id_gen: IdGenerator
    current_time: datetime | None = None
    completed_values: dict[TaskId, Any] = field(default_factory=dict)
    failed_errors: dict[TaskId, BaseException] = field(default_factory=dict)
    future_values: dict[FutureId, Any] = field(default_factory=dict)
    main_task_id: TaskId | None = None
    
    @classmethod
    def initial(
        cls,
        program: Program,
        env: Environment | dict[Any, Any] | None = None,
        store: Store | None = None,
        current_time: datetime | None = None,
    ) -> UnifiedCESKState:
        id_gen = IdGenerator()
        main_task_id = id_gen.next_task_id()
        
        if env is None:
            env_frozen = FrozenDict()
        elif isinstance(env, FrozenDict):
            env_frozen = env
        else:
            env_frozen = FrozenDict(env)
        
        main_task = TaskState.initial(main_task_id, program, env_frozen)
        
        return cls(
            tasks={main_task_id: main_task},
            store=store if store is not None else {},
            id_gen=id_gen,
            current_time=current_time,
            main_task_id=main_task_id,
        )
    
    def get_task(self, task_id: TaskId) -> TaskState | None:
        return self.tasks.get(task_id)
    
    def update_task(self, task: TaskState) -> UnifiedCESKState:
        new_tasks = {**self.tasks, task.task_id: task}
        return UnifiedCESKState(
            tasks=new_tasks,
            store=self.store,
            id_gen=self.id_gen,
            current_time=self.current_time,
            completed_values=self.completed_values,
            failed_errors=self.failed_errors,
            future_values=self.future_values,
            main_task_id=self.main_task_id,
        )
    
    def add_task(self, task: TaskState) -> UnifiedCESKState:
        return self.update_task(task)
    
    def remove_task(self, task_id: TaskId) -> UnifiedCESKState:
        new_tasks = {tid: t for tid, t in self.tasks.items() if tid != task_id}
        return UnifiedCESKState(
            tasks=new_tasks,
            store=self.store,
            id_gen=self.id_gen,
            current_time=self.current_time,
            completed_values=self.completed_values,
            failed_errors=self.failed_errors,
            future_values=self.future_values,
            main_task_id=self.main_task_id,
        )
    
    def complete_task(self, task_id: TaskId, value: Any) -> UnifiedCESKState:
        task = self.tasks.get(task_id)
        if task is None:
            return self
        
        completed_task = task.with_status(TaskStatus.COMPLETED)
        new_tasks = {**self.tasks, task_id: completed_task}
        new_completed = {**self.completed_values, task_id: value}
        
        return UnifiedCESKState(
            tasks=new_tasks,
            store=self.store,
            id_gen=self.id_gen,
            current_time=self.current_time,
            completed_values=new_completed,
            failed_errors=self.failed_errors,
            future_values=self.future_values,
            main_task_id=self.main_task_id,
        )
    
    def fail_task(self, task_id: TaskId, error: BaseException) -> UnifiedCESKState:
        task = self.tasks.get(task_id)
        if task is None:
            return self
        
        failed_task = task.with_status(TaskStatus.FAILED)
        new_tasks = {**self.tasks, task_id: failed_task}
        new_failed = {**self.failed_errors, task_id: error}
        
        return UnifiedCESKState(
            tasks=new_tasks,
            store=self.store,
            id_gen=self.id_gen,
            current_time=self.current_time,
            completed_values=self.completed_values,
            failed_errors=new_failed,
            future_values=self.future_values,
            main_task_id=self.main_task_id,
        )
    
    def with_store(self, store: Store) -> UnifiedCESKState:
        return UnifiedCESKState(
            tasks=self.tasks,
            store=store,
            id_gen=self.id_gen,
            current_time=self.current_time,
            completed_values=self.completed_values,
            failed_errors=self.failed_errors,
            future_values=self.future_values,
            main_task_id=self.main_task_id,
        )
    
    def with_time(self, time: datetime) -> UnifiedCESKState:
        return UnifiedCESKState(
            tasks=self.tasks,
            store=self.store,
            id_gen=self.id_gen,
            current_time=time,
            completed_values=self.completed_values,
            failed_errors=self.failed_errors,
            future_values=self.future_values,
            main_task_id=self.main_task_id,
        )
    
    def set_future(self, future_id: FutureId, value: Any) -> UnifiedCESKState:
        new_futures = {**self.future_values, future_id: value}
        return UnifiedCESKState(
            tasks=self.tasks,
            store=self.store,
            id_gen=self.id_gen,
            current_time=self.current_time,
            completed_values=self.completed_values,
            failed_errors=self.failed_errors,
            future_values=new_futures,
            main_task_id=self.main_task_id,
        )
    
    def runnable_tasks(self) -> list[TaskId]:
        return [
            tid for tid, task in self.tasks.items()
            if task.status == TaskStatus.RUNNING
        ]
    
    def blocked_tasks(self) -> list[tuple[TaskId, Condition]]:
        return [
            (tid, task.condition)
            for tid, task in self.tasks.items()
            if task.status == TaskStatus.BLOCKED and task.condition is not None
        ]
    
    def is_complete(self) -> bool:
        if self.main_task_id is None:
            return len(self.tasks) == 0
        main = self.tasks.get(self.main_task_id)
        return main is not None and main.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
        )
    
    def main_result(self) -> tuple[Any, bool] | None:
        if self.main_task_id is None:
            return None
        if self.main_task_id in self.completed_values:
            return (self.completed_values[self.main_task_id], True)
        if self.main_task_id in self.failed_errors:
            return (self.failed_errors[self.main_task_id], False)
        return None


__all__ = [
    "TaskStatus",
    "WaitingForFuture",
    "WaitingForTime",
    "WaitingForIO",
    "WaitingForAny",
    "WaitingForAll",
    "Condition",
    "TaskState",
    "UnifiedCESKState",
]
