from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from .base import Effect, EffectBase, create_effect_with_trace
from .spawn import Waitable

T = TypeVar("T")


@dataclass(frozen=True)
class RaceResult(Generic[T]):
    first: Waitable[T]
    value: T
    rest: tuple[Waitable[T], ...]


@dataclass(frozen=True)
class RaceEffect(EffectBase):
    futures: tuple[Waitable[Any], ...]

    def __post_init__(self) -> None:
        if not self.futures:
            raise ValueError("Race requires at least one Waitable")
        for i, f in enumerate(self.futures):
            if not isinstance(f, Waitable):
                raise TypeError(f"Race argument {i} must be Waitable, got {type(f).__name__}")


def race(*futures: Waitable[Any]) -> RaceEffect:
    return create_effect_with_trace(RaceEffect(futures=tuple(futures)))


def Race(*futures: Waitable[Any]) -> Effect:
    return create_effect_with_trace(
        RaceEffect(futures=tuple(futures)), skip_frames=3
    )


__all__ = [
    "Race",
    "RaceEffect",
    "RaceResult",
    "race",
]
