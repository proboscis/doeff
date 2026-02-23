"""Deterministic scheduled-program queue."""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScheduledProgram:
    run_at: float
    sequence: int
    program: Any


class TimeQueue:
    def __init__(self) -> None:
        self._items: list[tuple[float, int, Any]] = []
        self._sequence = 0

    def push(self, run_at: float, program: Any) -> None:
        self._sequence += 1
        heapq.heappush(self._items, (float(run_at), self._sequence, program))

    def pop_due(self, now: float) -> ScheduledProgram | None:
        if not self._items:
            return None

        run_at, sequence, program = self._items[0]
        if run_at > now:
            return None

        heapq.heappop(self._items)
        return ScheduledProgram(run_at=run_at, sequence=sequence, program=program)

    def next_time(self) -> float | None:
        if not self._items:
            return None
        return self._items[0][0]

    def __len__(self) -> int:
        return len(self._items)
