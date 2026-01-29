"""Wait effect for blocking on Future completion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from .base import Effect, EffectBase, create_effect_with_trace
from .spawn import Future

T = TypeVar("T")


@dataclass(frozen=True)
class WaitEffect(EffectBase):
    """Wait for a Future to complete and return its value."""

    future: Future[Any]

    def __post_init__(self) -> None:
        if not isinstance(self.future, Future):
            raise TypeError(
                f"future must be a Future, got {type(self.future).__name__}"
            )


def wait(future: Future[T]) -> WaitEffect:
    return create_effect_with_trace(WaitEffect(future=future))


def Wait(future: Future[T]) -> Effect:
    return create_effect_with_trace(WaitEffect(future=future), skip_frames=3)


__all__ = [
    "Wait",
    "WaitEffect",
    "wait",
]
