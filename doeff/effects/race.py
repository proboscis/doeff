from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import doeff_vm

from .base import Effect, create_effect_with_trace
from .spawn import TaskCancelledError, Waitable, is_task_cancelled, normalize_waitable

T = TypeVar("T")


@dataclass(frozen=True)
class RaceResult(Generic[T]):
    first: Waitable[T]
    value: T
    rest: tuple[Waitable[T], ...]


RaceEffect = doeff_vm.RaceEffect


def _validate_race_items(futures: tuple[Any, ...]) -> tuple[Waitable[Any], ...]:
    if not futures:
        raise ValueError("Race requires at least one Waitable")
    normalized: list[Waitable[Any]] = []
    for i, f in enumerate(futures):
        try:
            normalized.append(normalize_waitable(f))
        except TypeError as exc:
            raise TypeError(f"Race argument {i} must be Waitable, got {type(f).__name__}") from exc
    return tuple(normalized)


def race(*futures: Waitable[Any]) -> RaceEffect:
    validated = _validate_race_items(tuple(futures))
    return create_effect_with_trace(RaceEffect(validated))


def Race(*futures: Waitable[Any]):
    validated = _validate_race_items(tuple(futures))

    from doeff import do

    @do
    def _program():
        for waitable in validated:
            if is_task_cancelled(waitable):
                raise TaskCancelledError("Task was cancelled")

        value = yield create_effect_with_trace(RaceEffect(validated), skip_frames=3)
        return RaceResult(first=validated[0], value=value, rest=tuple(validated[1:]))

    return _program()


__all__ = [
    "Race",
    "RaceEffect",
    "RaceResult",
    "race",
]
