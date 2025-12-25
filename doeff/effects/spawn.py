"""Parallel task spawning effects."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Generic, Literal, TypeVar

from ._program_types import ProgramLike
from ._validators import ensure_dict_str_any, ensure_program_like, ensure_str
from .base import Effect, EffectBase, create_effect_with_trace, intercept_value


SpawnBackend = Literal["thread", "process", "ray"]

_VALID_BACKENDS: tuple[SpawnBackend, ...] = ("thread", "process", "ray")

T = TypeVar("T")


@dataclass(frozen=True)
class SpawnEffect(EffectBase):
    """Spawn execution of a program and return a Task handle."""

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
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "SpawnEffect":
        program = intercept_value(self.program, transform)
        if program is self.program:
            return self
        return replace(self, program=program)


@dataclass(frozen=True)
class Task(Generic[T]):
    """Handle for a spawned task."""

    backend: SpawnBackend
    _handle: Any = field(repr=False)
    _env_snapshot: dict[Any, Any] = field(default_factory=dict, repr=False)
    _state_snapshot: dict[str, Any] = field(default_factory=dict, repr=False)
    _parent_call_stack: tuple[Any, ...] = field(default_factory=tuple, repr=False)

    def join(self) -> Effect:
        return create_effect_with_trace(TaskJoinEffect(task=self), skip_frames=3)


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
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "TaskJoinEffect":
        return self


def spawn(
    program: ProgramLike,
    *,
    preferred_backend: SpawnBackend | None = None,
    **options: Any,
) -> SpawnEffect:
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
    return create_effect_with_trace(
        SpawnEffect(
            program=program,
            preferred_backend=preferred_backend,
            options=options,
        ),
        skip_frames=3,
    )


__all__ = [
    "SpawnBackend",
    "SpawnEffect",
    "Task",
    "TaskJoinEffect",
    "spawn",
    "Spawn",
]
