"""Parallel task spawning effects.

This module implements Spawn and Task effects for background task execution
as specified in SPEC-EFF-005-concurrency.md.

Design Decisions (from spec):
1. Store semantics: Snapshot at spawn time (isolated - child gets copy)
2. Error handling: Exception stored in Task until join (fire-and-forget friendly)
3. Cancellation: Follow asyncio conventions (cancel() is sync request, CancelledError on join)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Generic, Literal, TypeVar

from ._program_types import ProgramLike
from ._validators import ensure_dict_str_any, ensure_program_like, ensure_str
from .base import Effect, EffectBase, create_effect_with_trace, intercept_value

SpawnBackend = Literal["thread", "process", "ray"]

_VALID_BACKENDS: tuple[SpawnBackend, ...] = ("thread", "process", "ray")

T = TypeVar("T")


class TaskCancelledError(Exception):
    """Raised when joining a cancelled task.
    
    Following asyncio conventions, this is raised when:
    - cancel() was called on the task
    - The task was awaited via join()
    """


@dataclass(frozen=True)
class SpawnEffect(EffectBase):
    """Spawn execution of a program and return a Task handle.
    
    The spawned program runs in the background with an isolated snapshot
    of the current store. Changes made by the spawned task do not affect
    the parent's store.
    """

    program: ProgramLike
    preferred_backend: SpawnBackend | None = None
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        ensure_program_like(self.program, name="program")
        if self.preferred_backend is not None:
            ensure_str(self.preferred_backend, name="preferred_backend")
            if self.preferred_backend not in _VALID_BACKENDS:
                raise ValueError(
                    "preferred_backend must be one of 'thread', 'process', or 'ray', "
                    f"got {self.preferred_backend!r}"
                )
        ensure_dict_str_any(self.options, name="options")

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> SpawnEffect:
        program = intercept_value(self.program, transform)
        if program is self.program:
            return self
        return replace(self, program=program)


@dataclass(frozen=True)
class Task(Generic[T]):
    """Handle for a spawned task.
    
    Provides methods to:
    - join(): Wait for completion and get result
    - cancel(): Request task cancellation
    - is_done(): Check if task has completed (without blocking)
    """

    backend: SpawnBackend
    _handle: Any = field(repr=False)
    _env_snapshot: dict[Any, Any] = field(default_factory=dict, repr=False)
    _state_snapshot: dict[str, Any] = field(default_factory=dict, repr=False)

    def join(self) -> Effect:
        """Wait for the task to complete and return its result.
        
        Returns:
            Effect that yields the task's return value when executed.
            
        Raises:
            TaskCancelledError: If the task was cancelled.
            Exception: Any exception raised by the task is re-raised on join.
        """
        return create_effect_with_trace(TaskJoinEffect(task=self), skip_frames=3)

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
class TaskJoinEffect(EffectBase):
    """Join a spawned Task and return its result."""

    task: Task[Any]

    def __post_init__(self) -> None:
        if not isinstance(self.task, Task):
            raise TypeError(
                f"task must be Task, got {type(self.task).__name__}"
            )

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> TaskJoinEffect:
        return self


@dataclass(frozen=True)
class TaskCancelEffect(EffectBase):
    """Request cancellation of a spawned Task."""

    task: Task[Any]

    def __post_init__(self) -> None:
        if not isinstance(self.task, Task):
            raise TypeError(
                f"task must be Task, got {type(self.task).__name__}"
            )

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> TaskCancelEffect:
        return self


@dataclass(frozen=True)
class TaskIsDoneEffect(EffectBase):
    """Check if a spawned Task has completed."""

    task: Task[Any]

    def __post_init__(self) -> None:
        if not isinstance(self.task, Task):
            raise TypeError(
                f"task must be Task, got {type(self.task).__name__}"
            )

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> TaskIsDoneEffect:
        return self


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
    return create_effect_with_trace(
        SpawnEffect(
            program=program,
            preferred_backend=preferred_backend,
            options=options,
        ),
        skip_frames=3,
    )


__all__ = [
    "Spawn",
    "SpawnBackend",
    "SpawnEffect",
    "Task",
    "TaskCancelEffect",
    "TaskCancelledError",
    "TaskIsDoneEffect",
    "TaskJoinEffect",
    "spawn",
]
