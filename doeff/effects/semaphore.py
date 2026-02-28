
import warnings
from dataclasses import dataclass, field

import doeff_vm

from .base import Effect


@dataclass(frozen=True, slots=True)
class Semaphore:
    id: int
    _scheduler_state_id: int | None = field(default=None, repr=False, compare=False)
    _cleanup_on_del: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.id, int):
            raise TypeError(f"id must be int, got {type(self.id).__name__}")
        if self._scheduler_state_id is not None and not isinstance(self._scheduler_state_id, int):
            raise TypeError(
                "_scheduler_state_id must be int | None, "
                f"got {type(self._scheduler_state_id).__name__}"
            )
        if not isinstance(self._cleanup_on_del, bool):
            raise TypeError(
                f"_cleanup_on_del must be bool, got {type(self._cleanup_on_del).__name__}"
            )

    def __del__(self) -> None:
        if self._scheduler_state_id is None or not self._cleanup_on_del:
            return

        notify = getattr(doeff_vm, "_notify_semaphore_handle_dropped", None)
        if notify is None:
            return

        try:
            notify(self._scheduler_state_id, self.id)
        except Exception as exc:
            # Best-effort cleanup hook; never let destructor exceptions escape.
            warnings.warn(
                f"Failed to notify semaphore handle drop for id={self.id}: {exc}",
                stacklevel=2,
            )
            return


CreateSemaphoreEffect = doeff_vm.CreateSemaphoreEffect
AcquireSemaphoreEffect = doeff_vm.AcquireSemaphoreEffect
ReleaseSemaphoreEffect = doeff_vm.ReleaseSemaphoreEffect


def _ensure_permits(permits: int) -> None:
    if not isinstance(permits, int):
        raise TypeError(f"permits must be int, got {type(permits).__name__}")
    if permits < 1:
        raise ValueError("permits must be >= 1")


def _ensure_semaphore(semaphore: Semaphore) -> None:
    if not isinstance(semaphore, Semaphore):
        raise TypeError(f"semaphore must be Semaphore, got {type(semaphore).__name__}")


def create_semaphore(permits: int) -> CreateSemaphoreEffect:
    _ensure_permits(permits)
    return CreateSemaphoreEffect(permits=permits)


def acquire_semaphore(semaphore: Semaphore) -> AcquireSemaphoreEffect:
    _ensure_semaphore(semaphore)
    return AcquireSemaphoreEffect(semaphore=semaphore)


def release_semaphore(semaphore: Semaphore) -> ReleaseSemaphoreEffect:
    _ensure_semaphore(semaphore)
    return ReleaseSemaphoreEffect(semaphore=semaphore)


def CreateSemaphore(permits: int) -> Effect:  # noqa: N802
    _ensure_permits(permits)
    return CreateSemaphoreEffect(permits=permits)


def AcquireSemaphore(semaphore: Semaphore) -> Effect:  # noqa: N802
    _ensure_semaphore(semaphore)
    return AcquireSemaphoreEffect(semaphore=semaphore)


def ReleaseSemaphore(semaphore: Semaphore) -> Effect:  # noqa: N802
    _ensure_semaphore(semaphore)
    return ReleaseSemaphoreEffect(semaphore=semaphore)


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
