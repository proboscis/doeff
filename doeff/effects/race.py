from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import doeff_vm

from .base import Effect, create_effect_with_trace
from .spawn import Waitable

T = TypeVar("T")


@dataclass(frozen=True)
class RaceResult(Generic[T]):
    first: Waitable[T]
    value: T
    rest: tuple[Waitable[T], ...]


RaceEffect = doeff_vm.RaceEffect


def _validate_race_items(futures: tuple[Waitable[Any], ...]) -> tuple[Waitable[Any], ...]:
    if not futures:
        raise ValueError("Race requires at least one Waitable")
    for i, f in enumerate(futures):
        if not isinstance(f, Waitable):
            raise TypeError(f"Race argument {i} must be Waitable, got {type(f).__name__}")
    return futures


def race(*futures: Waitable[Any]) -> RaceEffect:
    validated = _validate_race_items(tuple(futures))
    return create_effect_with_trace(RaceEffect(validated))


def Race(*futures: Waitable[Any]) -> Effect:
    validated = _validate_race_items(tuple(futures))
    return create_effect_with_trace(RaceEffect(validated), skip_frames=3)


__all__ = [
    "Race",
    "RaceEffect",
    "RaceResult",
    "race",
]
