"""Min-heap queue of time-ordered promises."""


import heapq
import math
from dataclasses import dataclass
from typing import Any

from doeff.effects.spawn import Promise


def _coerce_finite_float(value: float, *, name: str) -> float:
    if not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be float, got {type(value).__name__}")
    coerced = float(value)
    if math.isnan(coerced) or math.isinf(coerced):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return coerced


@dataclass(frozen=True)
class TimeQueueEntry:
    time: float
    sequence: int
    promise: Promise[Any]


class TimeQueue:
    def __init__(self) -> None:
        self._sequence = 0
        self._items: list[tuple[float, int, Promise[Any]]] = []

    def push(self, time: float, promise: Promise[Any]) -> None:
        target_time = _coerce_finite_float(time, name="time")
        self._sequence += 1
        heapq.heappush(self._items, (target_time, self._sequence, promise))

    def pop(self) -> TimeQueueEntry:
        time, sequence, promise = heapq.heappop(self._items)
        return TimeQueueEntry(time=time, sequence=sequence, promise=promise)

    def empty(self) -> bool:
        return not self._items

    def __len__(self) -> int:
        return len(self._items)
