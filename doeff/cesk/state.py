"""CESK machine control, state, and request types for unified multi-task architecture."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable, TypeAlias

from doeff._vendor import FrozenDict, Result
from doeff._types_internal import EffectBase
from doeff.cesk.types import (
    Environment,
    FutureId,
    SpawnId,
    Store,
    TaskId,
)

if TYPE_CHECKING:
    from doeff.cesk_traceback import CapturedTraceback
    from doeff.cesk.frames import Frame, Kontinuation
    from doeff.effects.spawn import SpawnBackend
    from doeff.program import Program


@dataclass(frozen=True)
class ValueControl:
    v: Any


@dataclass(frozen=True)
class ErrorControl:
    ex: BaseException
    captured_traceback: CapturedTraceback | None = None


@dataclass(frozen=True)
class EffectControl:
    effect: EffectBase


@dataclass(frozen=True)
class ProgramControl:
    program: Program


Control: TypeAlias = ValueControl | ErrorControl | EffectControl | ProgramControl


@dataclass(frozen=True)
class TimeCondition:
    wake_time: datetime


@dataclass(frozen=True)
class FutureCondition:
    future_id: FutureId


@dataclass(frozen=True)
class TaskCondition:
    task_id: TaskId


@dataclass(frozen=True)
class SpawnCondition:
    spawn_id: SpawnId


Condition: TypeAlias = TimeCondition | FutureCondition | TaskCondition | SpawnCondition


@dataclass(frozen=True)
class CreateTaskRequest:
    program: Program


@dataclass(frozen=True)
class CreateFutureRequest:
    pass


@dataclass(frozen=True)
class ResolveFutureRequest:
    future_id: FutureId
    value: Any


@dataclass(frozen=True)
class PerformIORequest:
    action: Callable[[], Any]


@dataclass(frozen=True)
class AwaitExternalRequest:
    awaitable: Awaitable[Any]


@dataclass(frozen=True)
class CreateSpawnRequest:
    program: Program
    backend: SpawnBackend


Request: TypeAlias = (
    CreateTaskRequest
    | CreateFutureRequest
    | ResolveFutureRequest
    | PerformIORequest
    | AwaitExternalRequest
    | CreateSpawnRequest
)


@dataclass(frozen=True)
class ReadyStatus:
    resume_value: Any


@dataclass(frozen=True)
class BlockedStatus:
    condition: Condition


@dataclass(frozen=True)
class RequestingStatus:
    request: Request


@dataclass(frozen=True)
class DoneStatus:
    result: Result[Any]


TaskStatus: TypeAlias = ReadyStatus | BlockedStatus | RequestingStatus | DoneStatus


@dataclass
class TaskState:
    control: Control
    env: Environment
    store: Store
    kontinuation: Kontinuation
    status: TaskStatus = field(default_factory=lambda: ReadyStatus(None))
    
    @classmethod
    def initial(
        cls,
        program: Program,
        env: Environment | dict[Any, Any] | None = None,
        store: Store | None = None,
    ) -> TaskState:
        if env is None:
            env_frozen = FrozenDict()
        elif isinstance(env, FrozenDict):
            env_frozen = env
        else:
            env_frozen = FrozenDict(env)
        return cls(
            control=ProgramControl(program),
            env=env_frozen,
            store=store if store is not None else {},
            kontinuation=[],
            status=ReadyStatus(None),
        )
    
    def with_control(self, control: Control) -> TaskState:
        return TaskState(
            control=control,
            env=self.env,
            store=self.store,
            kontinuation=self.kontinuation,
            status=self.status,
        )
    
    def with_env(self, env: Environment) -> TaskState:
        return TaskState(
            control=self.control,
            env=env,
            store=self.store,
            kontinuation=self.kontinuation,
            status=self.status,
        )
    
    def with_store(self, store: Store) -> TaskState:
        return TaskState(
            control=self.control,
            env=self.env,
            store=store,
            kontinuation=self.kontinuation,
            status=self.status,
        )
    
    def with_kontinuation(self, kontinuation: Kontinuation) -> TaskState:
        return TaskState(
            control=self.control,
            env=self.env,
            store=self.store,
            kontinuation=kontinuation,
            status=self.status,
        )
    
    def with_status(self, status: TaskStatus) -> TaskState:
        return TaskState(
            control=self.control,
            env=self.env,
            store=self.store,
            kontinuation=self.kontinuation,
            status=status,
        )
    
    def push_frame(self, frame: Frame) -> TaskState:
        return TaskState(
            control=self.control,
            env=self.env,
            store=self.store,
            kontinuation=[frame] + self.kontinuation,
            status=self.status,
        )
    
    def resume_with(self, value: Any) -> TaskState:
        return TaskState(
            control=ValueControl(value),
            env=self.env,
            store=self.store,
            kontinuation=self.kontinuation,
            status=ReadyStatus(value),
        )
    
    def error_with(self, ex: BaseException, captured_traceback: CapturedTraceback | None = None) -> TaskState:
        return TaskState(
            control=ErrorControl(ex, captured_traceback),
            env=self.env,
            store=self.store,
            kontinuation=self.kontinuation,
            status=ReadyStatus(None),
        )


@dataclass
class CESKState:
    tasks: dict[TaskId, TaskState] = field(default_factory=dict)
    next_task_id: int = 0
    next_future_id: int = 0
    next_spawn_id: int = 0
    futures: dict[FutureId, Result[Any] | None] = field(default_factory=dict)
    spawns: dict[SpawnId, Result[Any] | None] = field(default_factory=dict)
    
    @classmethod
    def initial(
        cls,
        program: Program,
        env: Environment | dict[Any, Any] | None = None,
        store: Store | None = None,
    ) -> tuple[CESKState, TaskId]:
        state = cls()
        task_id, state = state.create_task(program, env, store)
        return state, task_id
    
    def create_task(
        self,
        program: Program,
        env: Environment | dict[Any, Any] | None = None,
        store: Store | None = None,
    ) -> tuple[TaskId, CESKState]:
        task_id = TaskId(self.next_task_id)
        task = TaskState.initial(program, env, store)
        new_tasks = {**self.tasks, task_id: task}
        new_state = CESKState(
            tasks=new_tasks,
            next_task_id=self.next_task_id + 1,
            next_future_id=self.next_future_id,
            next_spawn_id=self.next_spawn_id,
            futures=self.futures,
            spawns=self.spawns,
        )
        return task_id, new_state
    
    def with_task(self, task_id: TaskId, task: TaskState) -> CESKState:
        new_tasks = {**self.tasks, task_id: task}
        return CESKState(
            tasks=new_tasks,
            next_task_id=self.next_task_id,
            next_future_id=self.next_future_id,
            next_spawn_id=self.next_spawn_id,
            futures=self.futures,
            spawns=self.spawns,
        )
    
    def remove_task(self, task_id: TaskId) -> CESKState:
        new_tasks = {k: v for k, v in self.tasks.items() if k != task_id}
        return CESKState(
            tasks=new_tasks,
            next_task_id=self.next_task_id,
            next_future_id=self.next_future_id,
            next_spawn_id=self.next_spawn_id,
            futures=self.futures,
            spawns=self.spawns,
        )
    
    def create_future(self) -> tuple[FutureId, CESKState]:
        future_id = FutureId(self.next_future_id)
        new_futures = {**self.futures, future_id: None}
        new_state = CESKState(
            tasks=self.tasks,
            next_task_id=self.next_task_id,
            next_future_id=self.next_future_id + 1,
            next_spawn_id=self.next_spawn_id,
            futures=new_futures,
            spawns=self.spawns,
        )
        return future_id, new_state
    
    def resolve_future(self, future_id: FutureId, result: Result[Any]) -> CESKState:
        new_futures = {**self.futures, future_id: result}
        return CESKState(
            tasks=self.tasks,
            next_task_id=self.next_task_id,
            next_future_id=self.next_future_id,
            next_spawn_id=self.next_spawn_id,
            futures=new_futures,
            spawns=self.spawns,
        )
    
    def get_future_result(self, future_id: FutureId) -> Result[Any] | None:
        return self.futures.get(future_id)
    
    def create_spawn(self) -> tuple[SpawnId, CESKState]:
        spawn_id = SpawnId(self.next_spawn_id)
        new_spawns = {**self.spawns, spawn_id: None}
        new_state = CESKState(
            tasks=self.tasks,
            next_task_id=self.next_task_id,
            next_future_id=self.next_future_id,
            next_spawn_id=self.next_spawn_id + 1,
            futures=self.futures,
            spawns=new_spawns,
        )
        return spawn_id, new_state
    
    def resolve_spawn(self, spawn_id: SpawnId, result: Result[Any]) -> CESKState:
        new_spawns = {**self.spawns, spawn_id: result}
        return CESKState(
            tasks=self.tasks,
            next_task_id=self.next_task_id,
            next_future_id=self.next_future_id,
            next_spawn_id=self.next_spawn_id,
            futures=self.futures,
            spawns=new_spawns,
        )


Value = ValueControl
Error = ErrorControl


__all__ = [
    "ValueControl",
    "ErrorControl",
    "EffectControl",
    "ProgramControl",
    "Control",
    "TimeCondition",
    "FutureCondition",
    "TaskCondition",
    "SpawnCondition",
    "Condition",
    "CreateTaskRequest",
    "CreateFutureRequest",
    "ResolveFutureRequest",
    "PerformIORequest",
    "AwaitExternalRequest",
    "CreateSpawnRequest",
    "Request",
    "ReadyStatus",
    "BlockedStatus",
    "RequestingStatus",
    "DoneStatus",
    "TaskStatus",
    "TaskState",
    "CESKState",
    "Value",
    "Error",
]
