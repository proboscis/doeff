import doeff_vm

from .base import Effect

Semaphore = doeff_vm.Semaphore
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
        raise TypeError(
            "semaphore must be a Semaphore handle returned by CreateSemaphore, "
            f"got {type(semaphore).__name__}"
        )


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
