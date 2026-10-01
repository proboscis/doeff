"""Min-heap queue of time-ordered promises."""

import heapq
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from doeff_core_effects.scheduler import Promise

from doeff_time._internals.validation import ensure_aware_datetime

# ``Promise`` is generic to the type checker but not subscriptable at runtime.


@dataclass(frozen=True)
class TimeQueueEntry:
    time: datetime
    sequence: int
    promise: "Promise[Any]"


class TimeQueue:
    def __init__(self) -> None:
        self._mut_sequence = 0
        self._mut_items: "list[tuple[datetime, int, Promise[Any]]]" = []  # noqa: UP037
        # Sequences of entries withdrawn before their time (a timed wait whose future won —
        # agora-redesign #2618). Dropped lazily when they reach the head, so withdrawing is O(1)
        # and the clock never advances to a deadline nobody waits for.
        self._mut_withdrawn: set[int] = set()
        # Sequences still in the heap and not withdrawn — answers "is it still queued?" in O(1).
        self._mut_queued: set[int] = set()

    def push(self, time: datetime, promise: "Promise[Any]") -> int:
        """Queue ``promise`` to be completed at ``time``; answers the entry's sequence (for ``withdraw``)."""
        target_time = ensure_aware_datetime(time, name="time")
        self._mut_sequence += 1
        heapq.heappush(self._mut_items, (target_time, self._mut_sequence, promise))
        self._mut_queued.add(self._mut_sequence)
        return self._mut_sequence

    def withdraw(self, sequence: int) -> None:
        """Drop a queued entry before its time (no-op when it was already popped)."""
        if sequence in self._mut_queued:
            self._mut_queued.discard(sequence)
            self._mut_withdrawn.add(sequence)
            self._drop_withdrawn_head()

    def _drop_withdrawn_head(self) -> None:
        """Keep the head a live entry, so ``pop`` / ``empty`` never see a withdrawn deadline."""
        while self._mut_items and self._mut_items[0][1] in self._mut_withdrawn:
            _, sequence, _ = heapq.heappop(self._mut_items)
            self._mut_withdrawn.discard(sequence)

    def pop(self) -> TimeQueueEntry:
        self._drop_withdrawn_head()
        time, sequence, promise = heapq.heappop(self._mut_items)
        self._mut_queued.discard(sequence)
        self._drop_withdrawn_head()
        return TimeQueueEntry(time=time, sequence=sequence, promise=promise)

    def empty(self) -> bool:
        self._drop_withdrawn_head()
        return not self._mut_items

    def __len__(self) -> int:
        return len(self._mut_queued)
