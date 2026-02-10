from __future__ import annotations

from typing import Any, TypeVar

import doeff_vm

from .base import Effect, create_effect_with_trace
from .spawn import Promise, coerce_promise_handle

T = TypeVar("T")


CreatePromiseEffect = doeff_vm.CreatePromiseEffect
CompletePromiseEffect = doeff_vm.CompletePromiseEffect
FailPromiseEffect = doeff_vm.FailPromiseEffect


def CreatePromise():
    from doeff import do

    @do
    def _program():
        raw_promise = yield create_effect_with_trace(CreatePromiseEffect(), skip_frames=3)
        return coerce_promise_handle(raw_promise)

    return _program()


def CompletePromise(promise: Promise[T], value: T) -> Effect:
    wrapped_promise = coerce_promise_handle(promise)
    return create_effect_with_trace(
        CompletePromiseEffect(wrapped_promise, value),
        skip_frames=3,
    )


def FailPromise(promise: Promise[Any], error: BaseException) -> Effect:
    wrapped_promise = coerce_promise_handle(promise)
    if not isinstance(error, BaseException):
        raise TypeError(f"error must be BaseException, got {type(error).__name__}")
    return create_effect_with_trace(
        FailPromiseEffect(wrapped_promise, error),
        skip_frames=3,
    )


__all__ = [
    "CompletePromise",
    "CompletePromiseEffect",
    "CreatePromise",
    "CreatePromiseEffect",
    "FailPromise",
    "FailPromiseEffect",
]
