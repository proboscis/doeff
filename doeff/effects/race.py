"""Race effect - first Future to complete wins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from .base import Effect, EffectBase, create_effect_with_trace
from .spawn import Future

T = TypeVar("T")


@dataclass(frozen=True)
class RaceResult(Generic[T]):
    """Result of a Race effect."""

    first: Future[T]
    value: T
    rest: tuple[Future[T], ...]


@dataclass(frozen=True)
class RaceEffect(EffectBase):
    """Race multiple Futures - first to complete wins. Returns RaceResult."""

    futures: tuple[Future[Any], ...]

    def __post_init__(self) -> None:
        if not self.futures:
            raise ValueError("Race requires at least one Future")
        for i, f in enumerate(self.futures):
            if not isinstance(f, Future):
                raise TypeError(f"Race argument {i} must be a Future, got {type(f).__name__}")


def race(*futures: Future[Any]) -> RaceEffect:
    return create_effect_with_trace(RaceEffect(futures=tuple(futures)))


def Race(*futures: Future[Any]) -> Effect:
    return create_effect_with_trace(
        RaceEffect(futures=tuple(futures)), skip_frames=3
    )


__all__ = [
    "Race",
    "RaceEffect",
    "RaceResult",
    "race",
]
