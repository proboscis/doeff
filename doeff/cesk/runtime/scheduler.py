"""Unified scheduler for cooperative task execution.

The Scheduler provides a single coordination point for all ready work,
managing both internal tasks and external suspensions.
"""

from __future__ import annotations

from dataclasses import dataclass
from queue import Queue
from threading import Lock
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from doeff.cesk.result import Suspended
    from doeff.cesk.state import CESKState
    from doeff.cesk.types import Store, TaskId

T = TypeVar("T")


@dataclass
class ResumeWithValue:
    value: Any
    store: Store


@dataclass
class ResumeWithError:
    error: BaseException


@dataclass
class ResumeWithState:
    state: CESKState


@dataclass
class InitialState:
    state: CESKState


ResumeInfo = ResumeWithValue | ResumeWithError | ResumeWithState | InitialState


class Scheduler:
    """Single coordination point for all ready work.

    Manages a ready queue for tasks that can be stepped and a pending dict
    for tasks suspended on external completion.
    """

    def __init__(self) -> None:
        self._ready: Queue[tuple[TaskId, ResumeInfo]] = Queue()
        self._pending: dict[Any, tuple[TaskId, Suspended, Store]] = {}
        self._lock = Lock()

    def enqueue_ready(self, task_id: TaskId, resume_info: ResumeInfo) -> None:
        self._ready.put((task_id, resume_info))

    def suspend_on(
        self,
        handle: Any,
        task_id: TaskId,
        suspended: Suspended,
        store: Store,
    ) -> None:
        with self._lock:
            self._pending[handle] = (task_id, suspended, store)

    def complete(self, handle: Any, value: T) -> None:
        with self._lock:
            if handle not in self._pending:
                return
            task_id, suspended, store = self._pending.pop(handle)
        new_state = suspended.resume(value, store)
        self._ready.put((task_id, ResumeWithState(new_state)))

    def fail(self, handle: Any, error: BaseException) -> None:
        with self._lock:
            if handle not in self._pending:
                return
            task_id, suspended, _store = self._pending.pop(handle)
        new_state = suspended.resume_error(error)
        self._ready.put((task_id, ResumeWithState(new_state)))

    def get_next(self, timeout: float | None = None) -> tuple[TaskId, ResumeInfo] | None:
        """Get next ready task.

        Args:
            timeout: If None, blocks until a task is ready.
                    If 0, returns immediately (non-blocking).
                    If > 0, blocks for at most that many seconds.

        Returns:
            Tuple of (task_id, resume_info) or None if timeout elapsed.
        """
        try:
            if timeout is None:
                return self._ready.get(block=True)
            if timeout == 0:
                return self._ready.get(block=False)
            return self._ready.get(block=True, timeout=timeout)
        except Exception:
            return None

    def has_ready(self) -> bool:
        return not self._ready.empty()

    def has_pending(self) -> bool:
        with self._lock:
            return len(self._pending) > 0

    def is_empty(self) -> bool:
        with self._lock:
            return self._ready.empty() and len(self._pending) == 0


__all__ = [
    "InitialState",
    "ResumeInfo",
    "ResumeWithError",
    "ResumeWithState",
    "ResumeWithValue",
    "Scheduler",
]
