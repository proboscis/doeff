"""Min-heap queue of time-ordered promises."""


import heapq
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from doeff.effects.spawn import Promise

from .validation import ensure_aware_datetime


@dataclass(frozen=True)
class TimeQueueEntry:
    time: datetime
    sequence: int
    promise: Promise[Any]


class TimeQueue:
    def __init__(self) -> None:
        self._sequence = 0
        self._items: list[tuple[datetime, int, Promise[Any]]] = []

    def push(self, time: datetime, promise: Promise[Any]) -> None:
        target_time = ensure_aware_datetime(time, name="time")
        self._sequence += 1
        heapq.heappush(self._items, (target_time, self._sequence, promise))

    def pop(self) -> TimeQueueEntry:
        time, sequence, promise = heapq.heappop(self._items)
        return TimeQueueEntry(time=time, sequence=sequence, promise=promise)

    def empty(self) -> bool:
        return not self._items

    def __len__(self) -> int:
        return len(self._items)
