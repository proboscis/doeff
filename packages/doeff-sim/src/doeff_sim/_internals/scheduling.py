"""Deterministic time queue primitives."""

from __future__ import annotations

import heapq
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable

from doeff_sim._internals.sim_clock import SimClock
from doeff_sim._internals.task_tracking import TaskRecord, TaskRegistry


@dataclass(frozen=True)
class ScheduledProgram:
    run_at: float
    sequence: int
    program: Any


@dataclass(frozen=True)
class FutureCompletion:
    task_id: int
    sequence: int
    completed_at: float
    value: Any | None
    error: BaseException | None


@dataclass(frozen=True)
class WaiterRecord:
    task_id: int | None
    sequence: int


class TimeQueue:
    def __init__(self) -> None:
        self._items: list[tuple[float, int, Any]] = []

    def push(self, run_at: float, sequence: int, program: Any) -> None:
        heapq.heappush(self._items, (float(run_at), int(sequence), program))

    def pop_due(self, now: float) -> ScheduledProgram | None:
        if not self._items:
            return None
        run_at: float
        sequence: int
        program: Any
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


class SimScheduler:
    def __init__(self, *, start_time: float | None = None) -> None:
        self.clock: SimClock = SimClock(
            current_time=float(start_time) if start_time is not None else 0.0
        )
        self.ready: deque[int] = deque()
        self.time_heap: TimeQueue = TimeQueue()
        self.task_registry: TaskRegistry = TaskRegistry()
        self._sequence: int = 0
        self._completion_sequence: int = 0
        self._task_sequence: int = 0
        self._task_order: dict[int, int] = {}
        self._completions: dict[int, FutureCompletion] = {}
        self._waiters: dict[int, list[WaiterRecord]] = {}

    def next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def next_scheduled_time(self) -> float | None:
        return self.time_heap.next_time()

    def schedule_program(self, *, run_at: float, program: Any) -> None:
        sequence: int = self.next_sequence()
        self.time_heap.push(run_at=run_at, sequence=sequence, program=program)

    def pop_due_program(self, now: float) -> ScheduledProgram | None:
        return self.time_heap.pop_due(now)

    def create_task(self, program: Any) -> TaskRecord:
        record: TaskRecord = self.task_registry.create_task(program)
        self._task_sequence += 1
        self._task_order[record.task_id] = self._task_sequence
        self.ready.append(record.task_id)
        return record

    def has_task(self, task_id: int) -> bool:
        return self.task_registry.has_task(task_id)

    def task_order(self, task_id: int) -> int:
        return self._task_order.get(task_id, task_id)

    def order_task_ids(self, task_ids: Iterable[int]) -> list[int]:
        ordered: list[int] = sorted(task_ids, key=self.task_order)
        return ordered

    def mark_running(self, task_id: int) -> None:
        self._discard_ready(task_id)
        self.task_registry.mark_running(task_id)

    def mark_completed(self, task_id: int, value: Any) -> FutureCompletion:
        self.task_registry.mark_completed(task_id, value)
        completion: FutureCompletion = self._record_completion(
            task_id=task_id,
            value=value,
            error=None,
        )
        return completion

    def mark_failed(self, task_id: int, error: BaseException) -> FutureCompletion:
        self.task_registry.mark_failed(task_id, error)
        completion: FutureCompletion = self._record_completion(
            task_id=task_id,
            value=None,
            error=error,
        )
        return completion

    def completion_for(self, task_id: int) -> FutureCompletion | None:
        return self._completions.get(task_id)

    def register_waiter(self, task_id: int, *, waiter_task_id: int | None = None) -> None:
        sequence: int = self.next_sequence()
        record: WaiterRecord = WaiterRecord(task_id=waiter_task_id, sequence=sequence)
        self._waiters.setdefault(task_id, []).append(record)

    def pop_waiters(self, task_id: int) -> tuple[WaiterRecord, ...]:
        waiters: list[WaiterRecord] = self._waiters.pop(task_id, [])
        return tuple(waiters)

    def _discard_ready(self, task_id: int) -> None:
        if task_id in self.ready:
            self.ready.remove(task_id)

    def _record_completion(
        self,
        *,
        task_id: int,
        value: Any | None,
        error: BaseException | None,
    ) -> FutureCompletion:
        self._completion_sequence += 1
        sequence: int = self._completion_sequence
        completed_at: float = self.clock.current_time
        completion: FutureCompletion = FutureCompletion(
            task_id=task_id,
            sequence=sequence,
            completed_at=completed_at,
            value=value,
            error=error,
        )
        self._completions[task_id] = completion
        self._waiters.pop(task_id, None)
        return completion
