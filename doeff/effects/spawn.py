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
from typing import Any, Generic, Literal, TypeVar, runtime_checkable, Protocol

import doeff_vm

from ._program_types import ProgramLike
from ._validators import ensure_dict_str_any, ensure_program_like, ensure_str
from .base import Effect, EffectBase, create_effect_with_trace

SpawnBackend = Literal["thread", "process", "ray"]

_VALID_BACKENDS: tuple[SpawnBackend, ...] = ("thread", "process", "ray")

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


@runtime_checkable
class Waitable(Protocol[T_co]):
    @property
    def _handle(self) -> Any: ...


@dataclass(frozen=True)
class Future(Generic[T]):
    _handle: Any = field(repr=False)


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


@dataclass(frozen=True, eq=False)
class Task(Generic[T]):
    # eq=False: hashable by id since snapshot dicts are unhashable
    backend: SpawnBackend
    _handle: Any = field(repr=False)
    _env_snapshot: dict[Any, Any] = field(default_factory=dict, repr=False, hash=False)
    _state_snapshot: dict[str, Any] = field(default_factory=dict, repr=False, hash=False)

    def __hash__(self) -> int:
        return hash(self._handle)

    def cancel(self) -> Effect:
        """Request cancellation of the task.

        This is a synchronous operation that merely requests cancellation.
        The task may take time to actually cancel. Following asyncio conventions:
        - cancel() returns immediately
        - The task may continue running until its next yield point
        - join() on a cancelled task raises TaskCancelledError

        Returns:
            Effect that yields True if cancellation was requested successfully,
            False if the task has already completed.
        """
        return create_effect_with_trace(TaskCancelEffect(task=self), skip_frames=3)

    def is_done(self) -> Effect:
        """Check if the task has completed (success, error, or cancelled).

        This is a non-blocking check that returns immediately.

        Returns:
            Effect that yields True if the task is done, False otherwise.
        """
        return create_effect_with_trace(TaskIsDoneEffect(task=self), skip_frames=3)


@dataclass(frozen=True)
class TaskCancelEffect(EffectBase):
    """Request cancellation of a spawned Task."""

    task: Task[Any]

    def __post_init__(self) -> None:
        if not isinstance(self.task, Task):
            raise TypeError(f"task must be Task, got {type(self.task).__name__}")


@dataclass(frozen=True)
class TaskIsDoneEffect(EffectBase):
    """Check if a spawned Task has completed."""

    task: Task[Any]

    def __post_init__(self) -> None:
        if not isinstance(self.task, Task):
            raise TypeError(f"task must be Task, got {type(self.task).__name__}")


def spawn(
    program: ProgramLike,
    *,
    preferred_backend: SpawnBackend | None = None,
    **options: Any,
) -> SpawnEffect:
    """Spawn a program as a background task.

    Args:
        program: The program to run in the background.
        preferred_backend: Optional backend hint ("thread", "process", or "ray").
        **options: Additional backend-specific options.

    Returns:
        SpawnEffect that yields a Task handle when executed.
    """
    ensure_program_like(program, name="program")
    if preferred_backend is not None:
        ensure_str(preferred_backend, name="preferred_backend")
        if preferred_backend not in _VALID_BACKENDS:
            raise ValueError(
                "preferred_backend must be one of 'thread', 'process', or 'ray', "
                f"got {preferred_backend!r}"
            )
    ensure_dict_str_any(options, name="options")

    return create_effect_with_trace(
        SpawnEffect(
            program=program,
            preferred_backend=preferred_backend,
            options=options,
        )
    )


def Spawn(
    program: ProgramLike,
    *,
    preferred_backend: SpawnBackend | None = None,
    **options: Any,
) -> Effect:
    """Spawn a program as a background task (capitalized alias).

    Args:
        program: The program to run in the background.
        preferred_backend: Optional backend hint ("thread", "process", or "ray").
        **options: Additional backend-specific options.

    Returns:
        Effect that yields a Task handle when executed.
    """
    ensure_program_like(program, name="program")
    if preferred_backend is not None:
        ensure_str(preferred_backend, name="preferred_backend")
        if preferred_backend not in _VALID_BACKENDS:
            raise ValueError(
                "preferred_backend must be one of 'thread', 'process', or 'ray', "
                f"got {preferred_backend!r}"
            )
    ensure_dict_str_any(options, name="options")

    return create_effect_with_trace(
        SpawnEffect(
            program=program,
            preferred_backend=preferred_backend,
            options=options,
        ),
        skip_frames=3,
    )


__all__ = [
    "Future",
    "Promise",
    "Spawn",
    "SpawnBackend",
    "SpawnEffect",
    "Task",
    "TaskCancelEffect",
    "TaskCancelledError",
    "TaskIsDoneEffect",
    "Waitable",
    "spawn",
]
