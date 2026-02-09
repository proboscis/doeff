from __future__ import annotations

from typing import Any, TypeVar

import doeff_vm

from .base import Effect, create_effect_with_trace
from .spawn import Promise

T = TypeVar("T")


CreatePromiseEffect = doeff_vm.CreatePromiseEffect
CompletePromiseEffect = doeff_vm.CompletePromiseEffect
FailPromiseEffect = doeff_vm.FailPromiseEffect


def CreatePromise() -> Effect:
    return create_effect_with_trace(CreatePromiseEffect(), skip_frames=3)


def CompletePromise(promise: Promise[T], value: T) -> Effect:
    if not isinstance(promise, Promise):
        raise TypeError(f"promise must be Promise, got {type(promise).__name__}")
    return create_effect_with_trace(CompletePromiseEffect(promise, value), skip_frames=3)


def FailPromise(promise: Promise[Any], error: BaseException) -> Effect:
    if not isinstance(promise, Promise):
        raise TypeError(f"promise must be Promise, got {type(promise).__name__}")
    if not isinstance(error, BaseException):
        raise TypeError(f"error must be BaseException, got {type(error).__name__}")
    return create_effect_with_trace(FailPromiseEffect(promise, error), skip_frames=3)


__all__ = [
    "CompletePromise",
    "CompletePromiseEffect",
    "CreatePromise",
    "CreatePromiseEffect",
    "FailPromise",
    "FailPromiseEffect",
]
