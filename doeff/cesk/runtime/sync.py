"""Synchronous runtime for blocking execution.

SyncRuntime provides:
- Blocking I/O execution
- Real time waiting
- Simple single-threaded execution
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from doeff.cesk.runtime.base import BaseRuntime
from doeff.cesk.state import CESKState, TaskStatus
from doeff.cesk.types import TaskId
from doeff.cesk.step import resume_task, resume_task_error

if TYPE_CHECKING:
    from doeff.cesk.handlers import HandlerRegistry


class SyncRuntime(BaseRuntime):
    """Synchronous runtime with blocking I/O.

    Executes programs synchronously:
    - I/O operations block the thread
    - Time waits use real time.sleep()
    - Simple and predictable execution

    Example:
        runtime = SyncRuntime(handlers)
        result = runtime.run(my_program)
    """

    def __init__(self, handlers: HandlerRegistry) -> None:
        """Initialize sync runtime.

        Args:
            handlers: Handler registry
        """
        super().__init__(handlers)
        self._pending_io: dict[TaskId, tuple[Any, bool]] = {}  # task_id -> (operation, is_await)

    def run_to_completion(self, state: CESKState) -> CESKState:
        """Run all tasks synchronously."""
        while True:
            # Get runnable tasks
            runnable = self._get_runnable_tasks(state)

            if runnable:
                # Step the first runnable task
                task_id = runnable[0]
                output = self.step_once(state, task_id)
                state = self._process_events(output)
                continue

            # No runnable tasks
            if self._is_all_done(state):
                return state

            # Handle pending I/O
            if self._pending_io:
                state = self._execute_pending_io(state)
                continue

            # Check for time-waiting tasks
            waiting_tasks = [
                task for task in state.tasks.values()
                if task.status == TaskStatus.WAITING and task.condition is not None
            ]
            if waiting_tasks:
                # In sync mode, we can't do much with time waits
                # Just return for now
                break

            # All tasks blocked with nothing to do
            break

        return state

    def _execute_pending_io(self, state: CESKState) -> CESKState:
        """Execute pending I/O operations."""
        # Process one I/O operation
        task_id, (operation, is_await) = next(iter(self._pending_io.items()))
        del self._pending_io[task_id]

        if is_await:
            # Await operation - can't do in sync runtime
            # Just leave task blocked
            return state

        # I/O operation
        try:
            if callable(operation):
                result = operation()
            else:
                # Not callable - treat as error
                raise TypeError(f"I/O operation must be callable, got {type(operation)}")
            return resume_task(state, task_id, result)
        except Exception as ex:
            return resume_task_error(state, task_id, ex)

    def _handle_time_wait(
        self, state: CESKState, task_id: TaskId, target_time: float
    ) -> CESKState:
        """Handle wait until specific time - actually wait."""
        current = time.time()
        if target_time > current:
            wait_seconds = target_time - current
            time.sleep(wait_seconds)
        return resume_task(state, task_id, None)

    def _handle_duration_wait(
        self, state: CESKState, task_id: TaskId, seconds: float
    ) -> CESKState:
        """Handle wait for duration - actually sleep."""
        if seconds > 0:
            time.sleep(seconds)
        return resume_task(state, task_id, None)

    def _handle_io_request(
        self, state: CESKState, task_id: TaskId, operation: Any
    ) -> CESKState:
        """Handle I/O request - execute synchronously."""
        try:
            if callable(operation):
                result = operation()
            else:
                raise TypeError(f"I/O operation must be callable, got {type(operation)}")
            return resume_task(state, task_id, result)
        except Exception as ex:
            return resume_task_error(state, task_id, ex)

    def _handle_await_request(
        self, state: CESKState, task_id: TaskId, awaitable: Any
    ) -> CESKState:
        """Handle await request - not supported in sync runtime.

        Marks the I/O as pending for later handling.
        """
        self._pending_io[task_id] = (awaitable, True)
        return state


__all__ = [
    "SyncRuntime",
]
