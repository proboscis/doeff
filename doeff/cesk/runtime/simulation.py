"""Simulation runtime with controlled time.

SimulationRuntime provides:
- Deterministic execution for testing
- Controlled time advancement
- Mock I/O through callbacks
- No actual waiting or I/O
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from doeff.cesk.runtime.base import BaseRuntime
from doeff.cesk.state import CESKState, TaskState, TaskStatus, Value
from doeff.cesk.types import TaskId
from doeff.cesk.step import resume_task, resume_task_error

if TYPE_CHECKING:
    from doeff.cesk.handlers import HandlerRegistry


@dataclass(order=True)
class TimerEntry:
    """Entry in the simulation timer queue."""

    time: float
    task_id: TaskId = field(compare=False)
    callback: Callable[[CESKState], CESKState] | None = field(compare=False, default=None)


class SimulationRuntime(BaseRuntime):
    """Runtime for deterministic testing with simulated time.

    Features:
    - Time only advances when explicitly requested or when waiting
    - I/O operations use mock callbacks
    - Perfect for unit testing effect handlers

    Example:
        runtime = SimulationRuntime(handlers)
        runtime.set_io_mock(my_io_func, lambda: "mocked result")
        result = runtime.run(my_program)
    """

    def __init__(
        self,
        handlers: HandlerRegistry,
        initial_time: float = 0.0,
    ) -> None:
        """Initialize simulation runtime.

        Args:
            handlers: Handler registry
            initial_time: Starting simulated time (Unix timestamp)
        """
        super().__init__(handlers)
        self._current_time = initial_time
        self._timer_queue: list[TimerEntry] = []
        self._io_mocks: dict[Any, Callable[[], Any]] = {}
        self._await_mocks: dict[Any, Any] = {}

    @property
    def current_time(self) -> float:
        """Get current simulated time."""
        return self._current_time

    def advance_time(self, seconds: float) -> None:
        """Advance simulated time by given amount."""
        self._current_time += seconds

    def set_time(self, time: float) -> None:
        """Set simulated time to specific value."""
        self._current_time = time

    def set_io_mock(self, operation: Any, callback: Callable[[], Any]) -> None:
        """Set a mock callback for an I/O operation.

        Args:
            operation: The I/O operation function to mock
            callback: Function that returns the mock result
        """
        self._io_mocks[operation] = callback

    def set_await_mock(self, awaitable_type: type, result: Any) -> None:
        """Set a mock result for awaitable types.

        Args:
            awaitable_type: Type of awaitable to mock
            result: Value to return when awaited
        """
        self._await_mocks[awaitable_type] = result

    def run_to_completion(self, state: CESKState) -> CESKState:
        """Run all tasks with time simulation."""
        # Set initial time in store
        store = dict(state.S)
        store["__current_time__"] = self._current_time
        state = state.with_store(store)

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

            # Process timers
            if self._timer_queue:
                state = self._process_next_timer(state)
                continue

            # All tasks blocked with no timers - deadlock
            break

        return state

    def _process_next_timer(self, state: CESKState) -> CESKState:
        """Process the next timer in the queue."""
        if not self._timer_queue:
            return state

        entry = heapq.heappop(self._timer_queue)

        # Advance time to timer
        if entry.time > self._current_time:
            self._current_time = entry.time
            # Update time in store
            store = dict(state.S)
            store["__current_time__"] = self._current_time
            state = state.with_store(store)

        # Execute callback if present
        if entry.callback is not None:
            return entry.callback(state)

        # Otherwise wake the task
        task = state.tasks.get(entry.task_id)
        if task is not None and task.status in (TaskStatus.WAITING, TaskStatus.BLOCKED):
            return resume_task(state, entry.task_id, None)

        return state

    def _handle_time_wait(
        self, state: CESKState, task_id: TaskId, target_time: float
    ) -> CESKState:
        """Handle wait until specific time."""
        if target_time <= self._current_time:
            # Already past - wake immediately
            return resume_task(state, task_id, None)

        # Schedule wake up
        entry = TimerEntry(time=target_time, task_id=task_id)
        heapq.heappush(self._timer_queue, entry)
        return state

    def _handle_duration_wait(
        self, state: CESKState, task_id: TaskId, seconds: float
    ) -> CESKState:
        """Handle wait for duration."""
        if seconds <= 0:
            # No wait needed
            return resume_task(state, task_id, None)

        # Schedule wake up
        target_time = self._current_time + seconds
        entry = TimerEntry(time=target_time, task_id=task_id)
        heapq.heappush(self._timer_queue, entry)
        return state

    def _handle_io_request(
        self, state: CESKState, task_id: TaskId, operation: Any
    ) -> CESKState:
        """Handle I/O request using mocks."""
        # Check for mock
        mock = self._io_mocks.get(operation)
        if mock is not None:
            try:
                result = mock()
                return resume_task(state, task_id, result)
            except Exception as ex:
                return resume_task_error(state, task_id, ex)

        # No mock - try to call directly (for simple callables)
        if callable(operation):
            try:
                result = operation()
                return resume_task(state, task_id, result)
            except Exception as ex:
                return resume_task_error(state, task_id, ex)

        # Can't handle - leave blocked
        return state

    def _handle_await_request(
        self, state: CESKState, task_id: TaskId, awaitable: Any
    ) -> CESKState:
        """Handle await request using mocks."""
        # Check for mock by type
        awaitable_type = type(awaitable)
        if awaitable_type in self._await_mocks:
            result = self._await_mocks[awaitable_type]
            return resume_task(state, task_id, result)

        # Can't handle - leave blocked
        return state


__all__ = [
    "SimulationRuntime",
    "TimerEntry",
]
