"""Priority queue of scheduled virtual-time actions."""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TimeQueueEntry:
    time: float
    sequence: int
    promise: Any | None = None
    program: Any | None = None

    @property
    def is_promise(self) -> bool:
        return self.promise is not None

    @property
    def is_program(self) -> bool:
        return self.program is not None


class TimeQueue:
    """Min-heap ordered by (time, entry-kind, insertion sequence)."""

    def __init__(self) -> None:
        self._items: list[tuple[float, int, int, TimeQueueEntry]] = []
        self._mut_sequence: int = 0

    def push_promise(self, target_time: float, promise: Any) -> TimeQueueEntry:
        self._mut_sequence += 1
        entry = TimeQueueEntry(time=float(target_time), sequence=self._mut_sequence, promise=promise)
        heapq.heappush(self._items, (entry.time, 1, entry.sequence, entry))
        return entry

    def push_program(self, target_time: float, program: Any) -> TimeQueueEntry:
        self._mut_sequence += 1
        entry = TimeQueueEntry(time=float(target_time), sequence=self._mut_sequence, program=program)
        heapq.heappush(self._items, (entry.time, 0, entry.sequence, entry))
        return entry

    def pop(self) -> TimeQueueEntry | None:
        if not self._items:
            return None
        return heapq.heappop(self._items)[3]

    def empty(self) -> bool:
        return not self._items

    def __len__(self) -> int:
        return len(self._items)
