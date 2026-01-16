"""Action types returned by handlers to the step function."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Awaitable, TypeAlias

from doeff.cesk.types import Environment, FutureId, SpawnId, Store, TaskId

if TYPE_CHECKING:
    from doeff.effects._program_types import ProgramLike
    from doeff.program import Program


@dataclass(frozen=True)
class Resume:
    value: Any
    store: Store | None = None


@dataclass(frozen=True)
class ResumeError:
    error: BaseException
    store: Store | None = None


@dataclass(frozen=True)
class CreateTask:
    program: Program
    env: Environment | None = None
    spawn_id: SpawnId | None = None
    parent_id: TaskId | None = None


@dataclass(frozen=True)
class CreateTasks:
    programs: tuple[Program, ...]
    envs: tuple[Environment | None, ...] | None = None


@dataclass(frozen=True)
class PerformIO:
    io_callable: Any
    io_id: int = 0


@dataclass(frozen=True)
class AwaitExternal:
    awaitable: Awaitable[Any]
    future_id: FutureId


@dataclass(frozen=True)
class Delay:
    duration: timedelta


@dataclass(frozen=True)
class WaitUntil:
    target: datetime


@dataclass(frozen=True)
class CancelTasks:
    task_ids: frozenset[TaskId]


@dataclass(frozen=True)
class RunProgram:
    program: ProgramLike
    env: Environment | None = None


@dataclass(frozen=True)
class BlockForFuture:
    future_id: FutureId


@dataclass(frozen=True)
class BlockForTasks:
    task_ids: frozenset[TaskId]
    wait_all: bool = True


@dataclass(frozen=True)
class ModifyStore:
    key: str
    value: Any


@dataclass(frozen=True)
class AppendLog:
    message: Any


Action: TypeAlias = (
    Resume
    | ResumeError
    | CreateTask
    | CreateTasks
    | PerformIO
    | AwaitExternal
    | Delay
    | WaitUntil
    | CancelTasks
    | RunProgram
    | BlockForFuture
    | BlockForTasks
    | ModifyStore
    | AppendLog
)


__all__ = [
    "Resume",
    "ResumeError",
    "CreateTask",
    "CreateTasks",
    "PerformIO",
    "AwaitExternal",
    "Delay",
    "WaitUntil",
    "CancelTasks",
    "RunProgram",
    "BlockForFuture",
    "BlockForTasks",
    "ModifyStore",
    "AppendLog",
    "Action",
]
