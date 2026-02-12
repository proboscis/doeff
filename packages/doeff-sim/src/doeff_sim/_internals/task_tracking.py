"""Task bookkeeping for deterministic simulation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TaskRecord:
    task_id: int
    program: Any
    handle: dict[str, Any]
    status: str = "pending"
    result: Any = None
    error: BaseException | None = None
    awaited: bool = False


class TaskRegistry:
    def __init__(self) -> None:
        self._next_task_id = 1
        self._tasks: dict[int, TaskRecord] = {}

    def create_task(self, program: Any) -> TaskRecord:
        task_id = self._next_task_id
        self._next_task_id += 1

        handle = {
            "type": "Task",
            "task_id": task_id,
        }
        record = TaskRecord(task_id=task_id, program=program, handle=handle)
        self._tasks[task_id] = record
        return record

    def has_task(self, task_id: int) -> bool:
        return task_id in self._tasks

    def get(self, task_id: int) -> TaskRecord:
        return self._tasks[task_id]

    def mark_running(self, task_id: int) -> None:
        self._tasks[task_id].status = "running"

    def mark_completed(self, task_id: int, value: Any) -> None:
        record = self._tasks[task_id]
        record.status = "completed"
        record.result = value
        record.error = None

    def mark_failed(self, task_id: int, error: BaseException) -> None:
        record = self._tasks[task_id]
        record.status = "failed"
        record.error = error
