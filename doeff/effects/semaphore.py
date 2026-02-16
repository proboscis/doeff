from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from itertools import count
from typing import Any

import doeff_vm

from .base import Effect, EffectBase, create_effect_with_trace
from .promise import CompletePromise, CreatePromise
from .wait import Wait


@dataclass(slots=True)
class _SemaphoreWaiter:
    promise: Any
    cancelled: bool = False
    granted: bool = False


@dataclass(slots=True)
class _SemaphoreState:
    max_permits: int
    available_permits: int
    waiters: deque[_SemaphoreWaiter] = field(default_factory=deque)


@dataclass(frozen=True, slots=True)
class Semaphore:
    id: int
    _state: _SemaphoreState = field(repr=False, compare=False, hash=False)


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


_SEMAPHORE_ID_COUNTER = count(1)


def _drop_cancelled_head_waiters(state: _SemaphoreState) -> None:
    while state.waiters and state.waiters[0].cancelled:
        state.waiters.popleft()


def _remove_waiter(state: _SemaphoreState, target: _SemaphoreWaiter) -> bool:
    try:
        state.waiters.remove(target)
        return True
    except ValueError:
        return False


def _release_one(semaphore: Semaphore):
    state = semaphore._state

    while state.waiters:
        waiter = state.waiters.popleft()
        if waiter.cancelled:
            continue
        waiter.granted = True
        yield CompletePromise(waiter.promise, None)
        return

    if state.available_permits >= state.max_permits:
        raise RuntimeError("semaphore released too many times")

    state.available_permits += 1


def _handle_create_semaphore(effect: CreateSemaphoreEffect, k: Any):
    sem = Semaphore(
        id=next(_SEMAPHORE_ID_COUNTER),
        _state=_SemaphoreState(
            max_permits=effect.permits,
            available_permits=effect.permits,
        ),
    )
    return (yield doeff_vm.Resume(k, sem))


def _handle_acquire_semaphore(effect: AcquireSemaphoreEffect, k: Any):
    semaphore = effect.semaphore
    state = semaphore._state

    _drop_cancelled_head_waiters(state)
    if state.available_permits > 0 and not state.waiters:
        state.available_permits -= 1
        return (yield doeff_vm.Resume(k, None))

    waiter_promise = yield CreatePromise()
    waiter = _SemaphoreWaiter(promise=waiter_promise)
    state.waiters.append(waiter)

    try:
        _ = yield Wait(waiter_promise.future)
    except BaseException:
        waiter.cancelled = True
        removed = _remove_waiter(state, waiter)
        if not removed and waiter.granted:
            yield from _release_one(semaphore)
        raise

    return (yield doeff_vm.Resume(k, None))


def _handle_release_semaphore(effect: ReleaseSemaphoreEffect, k: Any):
    yield from _release_one(effect.semaphore)
    return (yield doeff_vm.Resume(k, None))


def _with_semaphore_handlers(program: Any) -> Any:
    from doeff.rust_vm import wrap_with_handler_map

    return wrap_with_handler_map(
        program,
        {
            CreateSemaphoreEffect: _handle_create_semaphore,
            AcquireSemaphoreEffect: _handle_acquire_semaphore,
            ReleaseSemaphoreEffect: _handle_release_semaphore,
        },
    )


def create_semaphore(permits: int) -> CreateSemaphoreEffect:
    return create_effect_with_trace(CreateSemaphoreEffect(permits=permits))


def acquire_semaphore(semaphore: Semaphore) -> AcquireSemaphoreEffect:
    return create_effect_with_trace(AcquireSemaphoreEffect(semaphore=semaphore))


def release_semaphore(semaphore: Semaphore) -> ReleaseSemaphoreEffect:
    return create_effect_with_trace(ReleaseSemaphoreEffect(semaphore=semaphore))


def CreateSemaphore(permits: int) -> Effect:
    return _with_semaphore_handlers(
        create_effect_with_trace(CreateSemaphoreEffect(permits=permits), skip_frames=3)
    )


def AcquireSemaphore(semaphore: Semaphore) -> Effect:
    return _with_semaphore_handlers(
        create_effect_with_trace(AcquireSemaphoreEffect(semaphore=semaphore), skip_frames=3)
    )


def ReleaseSemaphore(semaphore: Semaphore) -> Effect:
    return _with_semaphore_handlers(
        create_effect_with_trace(ReleaseSemaphoreEffect(semaphore=semaphore), skip_frames=3)
    )


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
