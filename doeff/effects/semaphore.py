from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module

from .base import Effect, EffectBase, create_effect_with_trace


@dataclass(frozen=True, slots=True)
class Semaphore:
    id: int
    _scheduler_state_id: int | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.id, int):
            raise TypeError(f"id must be int, got {type(self.id).__name__}")
        if self._scheduler_state_id is not None and not isinstance(self._scheduler_state_id, int):
            raise TypeError(
                f"_scheduler_state_id must be int|None, got {type(self._scheduler_state_id).__name__}"
            )

    def __del__(self) -> None:
        scheduler_state_id = self._scheduler_state_id
        if scheduler_state_id is None:
            return

        try:
            vm_ext = import_module("doeff_vm.doeff_vm")
            cleanup = getattr(vm_ext, "_scheduler_remove_semaphore", None)
            if callable(cleanup):
                cleanup(scheduler_state_id, self.id)
        except Exception:
            # __del__ must never raise.
            return


@dataclass(frozen=True)
class CreateSemaphoreEffect(EffectBase):
    permits: int

    def __post_init__(self) -> None:
        if not isinstance(self.permits, int):
            raise TypeError(f"permits must be int, got {type(self.permits).__name__}")
        if self.permits < 1:
            raise ValueError("permits must be >= 1")


@dataclass(frozen=True)
class AcquireSemaphoreEffect(EffectBase):
    semaphore: Semaphore

    def __post_init__(self) -> None:
        if not isinstance(self.semaphore, Semaphore):
            raise TypeError(f"semaphore must be Semaphore, got {type(self.semaphore).__name__}")


@dataclass(frozen=True)
class ReleaseSemaphoreEffect(EffectBase):
    semaphore: Semaphore

    def __post_init__(self) -> None:
        if not isinstance(self.semaphore, Semaphore):
            raise TypeError(f"semaphore must be Semaphore, got {type(self.semaphore).__name__}")


def create_semaphore(permits: int) -> CreateSemaphoreEffect:
    return create_effect_with_trace(CreateSemaphoreEffect(permits=permits))


def acquire_semaphore(semaphore: Semaphore) -> AcquireSemaphoreEffect:
    return create_effect_with_trace(AcquireSemaphoreEffect(semaphore=semaphore))


def release_semaphore(semaphore: Semaphore) -> ReleaseSemaphoreEffect:
    return create_effect_with_trace(ReleaseSemaphoreEffect(semaphore=semaphore))


def CreateSemaphore(permits: int) -> Effect:  # noqa: N802
    return create_effect_with_trace(CreateSemaphoreEffect(permits=permits), skip_frames=3)


def AcquireSemaphore(semaphore: Semaphore) -> Effect:  # noqa: N802
    return create_effect_with_trace(AcquireSemaphoreEffect(semaphore=semaphore), skip_frames=3)


def ReleaseSemaphore(semaphore: Semaphore) -> Effect:  # noqa: N802
    return create_effect_with_trace(ReleaseSemaphoreEffect(semaphore=semaphore), skip_frames=3)


__all__ = [
    "AcquireSemaphore",
    "AcquireSemaphoreEffect",
    "CreateSemaphore",
    "CreateSemaphoreEffect",
    "ReleaseSemaphore",
    "ReleaseSemaphoreEffect",
    "Semaphore",
    "acquire_semaphore",
    "create_semaphore",
    "release_semaphore",
]
