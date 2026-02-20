"""Parallel task spawning effects.

This module implements Spawn and Task effects for background task execution
as specified in SPEC-EFF-005-concurrency.md.

Design Decisions (from spec):
1. Store semantics: Snapshot at spawn time (isolated - child gets copy)
2. Error handling: Exception stored in Task until join (fire-and-forget friendly)
3. Cancellation: Follow asyncio conventions (cancel() is sync request, CancelledError on join)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, runtime_checkable, Protocol

import doeff_vm

from ._program_types import ProgramLike
from ._validators import ensure_dict_str_any, ensure_program_like
from .base import Effect, EffectBase

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)

_WAITABLE_TYPES: tuple[str, ...] = ("Task", "Promise", "ExternalPromise")


@runtime_checkable
class Waitable(Protocol[T_co]):
    @property  # nosemgrep: doeff-no-typing-any-in-public-api
    def _handle(self) -> Any:  # nosemgrep: doeff-no-typing-any-in-public-api
        ...


@dataclass(frozen=True)
class Future(Generic[T]):
    _handle: Any = field(repr=False)
    _completion_queue: Any | None = field(default=None, repr=False)


@dataclass
class Promise(Generic[T]):
    _promise_handle: Any = field(repr=False)
    _completed: bool = field(default=False, repr=False)
    _value: T | None = field(default=None, repr=False)
    _error: BaseException | None = field(default=None, repr=False)

    @property
    def future(self) -> Future[T]:
        return Future(_handle=self._promise_handle)

    @property
    def is_completed(self) -> bool:
        return self._completed

    @property
    def value(self) -> T | None:
        return self._value

    @property
    def error(self) -> BaseException | None:
        return self._error


class TaskCancelledError(Exception):
    """Raised when joining a cancelled task.

    Following asyncio conventions, this is raised when:
    - cancel() was called on the task
    - The task was awaited via join()
    """


SpawnEffect = doeff_vm.SpawnEffect
TaskCancelEffect = doeff_vm.PyCancelEffect


@dataclass(frozen=True, eq=False)
class Task(Generic[T]):
    # eq=False: hashable by id since snapshot dicts are unhashable
    _handle: Any = field(repr=False)
    _env_snapshot: dict[Any, Any] = field(default_factory=dict, repr=False, hash=False)
    _state_snapshot: dict[str, Any] = field(default_factory=dict, repr=False, hash=False)

    def __hash__(self) -> int:
        return hash(self._handle)

    def cancel(self):
        """Request cancellation of the task.

        Cancellation is a scheduler operation and must be dispatched as an effect.
        """
        return TaskCancelEffect(task=self)

    def is_done(self) -> Effect:
        """Check if the task has completed (success, error, or cancelled).

        This is a non-blocking check that returns immediately.

        Returns:
            Effect that yields True if the task is done, False otherwise.
        """
        return TaskIsDoneEffect(task=self)


@dataclass(frozen=True)
class TaskIsDoneEffect(EffectBase):
    """Check if a spawned Task has completed."""

    task: Task[Any]

    def __post_init__(self) -> None:
        if not isinstance(self.task, Task):
            raise TypeError(f"task must be Task, got {type(self.task).__name__}")


def _is_handle_dict(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    kind = value.get("type")
    if kind == "Task":
        return isinstance(value.get("task_id"), int)
    if kind in {"Promise", "ExternalPromise"}:
        return isinstance(value.get("promise_id"), int)
    return False


def task_id_of(value: Any) -> int | None:
    handle: Any
    if isinstance(value, Task):
        handle = value._handle
    elif isinstance(value, Future):
        handle = value._handle
    elif _is_handle_dict(value):
        handle = value
    else:
        return None

    if isinstance(handle, dict) and handle.get("type") == "Task":
        raw = handle.get("task_id")
        if isinstance(raw, int):
            return raw
    return None


def promise_id_of(value: Any) -> int | None:
    if isinstance(value, Promise):
        handle = value._promise_handle
    elif isinstance(value, Future):
        handle = value._handle
    elif _is_handle_dict(value):
        handle = value
    else:
        return None

    if isinstance(handle, dict):
        kind = handle.get("type")
        raw = handle.get("promise_id")
        if kind in {"Promise", "ExternalPromise"} and isinstance(raw, int):
            return raw
    return None


def coerce_task_handle(value: Any) -> Task[Any]:
    if isinstance(value, Task):
        return value
    if _is_handle_dict(value) and value.get("type") == "Task":
        return Task(_handle=value)
    raise TypeError(f"expected Task handle, got {type(value).__name__}")


def coerce_promise_handle(value: Any) -> Promise[Any]:
    if isinstance(value, Promise):
        return value
    if _is_handle_dict(value) and value.get("type") in {"Promise", "ExternalPromise"}:
        return Promise(_promise_handle=value)
    raise TypeError(f"expected Promise handle, got {type(value).__name__}")


def normalize_waitable(value: Any) -> Waitable[Any]:
    if isinstance(value, Waitable):
        return value
    if _is_handle_dict(value):
        if value.get("type") == "Task":
            return coerce_task_handle(value)
        return Future(_handle=value)
    raise TypeError(
        "waitable must be Waitable or scheduler handle dict with type/task_id|promise_id"
    )


def _spawn_program(effect: SpawnEffect):
    from doeff import do

    @do
    def _spawn_task():
        raw_task = yield effect
        return coerce_task_handle(raw_task)

    return _spawn_task()


def spawn(  # nosemgrep: doeff-no-typing-any-in-public-api
    program: ProgramLike,
    **options: Any,
) -> Any:
    """Spawn a program as a background task.

    Args:
        program: The program to run in the background.
        **options: Additional backend-specific options.

    Returns:
        SpawnEffect that yields a Task handle when executed.
    """
    ensure_program_like(program, name="program")
    ensure_dict_str_any(options, name="options")

    effect = SpawnEffect(
        program=program,
        options=options,
        store_mode="isolated",
    )
    return _spawn_program(effect)


def Spawn(  # nosemgrep: doeff-no-typing-any-in-public-api
    program: ProgramLike,
    **options: Any,
) -> Any:
    """Spawn a program as a background task (capitalized alias).

    Args:
        program: The program to run in the background.
        **options: Additional backend-specific options.

    Returns:
        Effect that yields a Task handle when executed.
    """
    ensure_program_like(program, name="program")
    ensure_dict_str_any(options, name="options")

    effect = SpawnEffect(
        program=program,
        options=options,
        store_mode="isolated",
    )
    return _spawn_program(effect)


__all__ = [
    "Future",
    "Promise",
    "Spawn",
    "SpawnEffect",
    "Task",
    "TaskCancelEffect",
    "TaskCancelledError",
    "TaskIsDoneEffect",
    "Waitable",
    "coerce_promise_handle",
    "coerce_task_handle",
    "promise_id_of",
    "spawn",
    "task_id_of",
]
