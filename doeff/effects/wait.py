from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from .base import Effect, EffectBase, create_effect_with_trace
from .spawn import Waitable

T = TypeVar("T")


@dataclass(frozen=True)
class WaitEffect(EffectBase):
    future: Waitable[Any]

    def __post_init__(self) -> None:
        if not isinstance(self.future, Waitable):
            raise TypeError(
                f"Wait requires Waitable, got {type(self.future).__name__}"
            )


def wait(future: Waitable[T]) -> WaitEffect:
    return create_effect_with_trace(WaitEffect(future=future))


def Wait(future: Waitable[T]) -> Effect:
    return create_effect_with_trace(WaitEffect(future=future), skip_frames=3)


__all__ = [
    "Wait",
    "WaitEffect",
    "wait",
]
