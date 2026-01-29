"""Promise effects for user-level Future creation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from .base import Effect, EffectBase, create_effect_with_trace
from .spawn import Promise

T = TypeVar("T")


@dataclass(frozen=True)
class CreatePromiseEffect(EffectBase):
    pass


@dataclass(frozen=True)
class CompletePromiseEffect(EffectBase):

    promise: Promise[Any]
    value: Any

    def __post_init__(self) -> None:
        if not isinstance(self.promise, Promise):
            raise TypeError(
                f"promise must be Promise, got {type(self.promise).__name__}"
            )


@dataclass(frozen=True)
class FailPromiseEffect(EffectBase):

    promise: Promise[Any]
    error: BaseException

    def __post_init__(self) -> None:
        if not isinstance(self.promise, Promise):
            raise TypeError(
                f"promise must be Promise, got {type(self.promise).__name__}"
            )
        if not isinstance(self.error, BaseException):
            raise TypeError(
                f"error must be BaseException, got {type(self.error).__name__}"
            )


def CreatePromise() -> Effect:
    return create_effect_with_trace(CreatePromiseEffect(), skip_frames=3)


def CompletePromise(promise: Promise[T], value: T) -> Effect:
    return create_effect_with_trace(
        CompletePromiseEffect(promise=promise, value=value), skip_frames=3
    )


def FailPromise(promise: Promise[Any], error: BaseException) -> Effect:
    return create_effect_with_trace(
        FailPromiseEffect(promise=promise, error=error), skip_frames=3
    )


__all__ = [
    "CompletePromise",
    "CompletePromiseEffect",
    "CreatePromise",
    "CreatePromiseEffect",
    "FailPromise",
    "FailPromiseEffect",
]
