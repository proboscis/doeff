"""Base runtime with common logic for all runtime implementations.

This module provides BaseRuntime with:
- Common event processing logic
- Task scheduling (round-robin by default)
- Future resolution and waiter notification
- Step-until-done execution loop
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from doeff.cesk.state import (
    CESKState,
    Error,
    FutureState,
    TaskState,
    TaskStatus,
    Value,
    WaitingOn,
    GatherCondition,
    RaceCondition,
)
from doeff.cesk.types import FutureId, TaskId
from doeff.cesk.step import StepOutput, step, resume_task, resume_task_error
from doeff.cesk.events import (
    AwaitRequested,
    Event,
    FutureRejected,
    FutureResolved,
    IORequested,
    TaskBlocked,
    TaskCancelled,
    TaskCreated,
    TaskDone,
    TaskFailed,
    TaskRacing,
    TaskReady,
    TasksCreated,
    TaskWaitingForDuration,
    TaskWaitingOnFuture,
    TaskWaitingOnFutures,
    TaskWaitingUntilTime,
)

if TYPE_CHECKING:
    from doeff.cesk.handlers import HandlerRegistry
    from doeff.program import Program


class BaseRuntime(ABC):
    """Base class for runtime implementations.

    Provides common logic for:
    - Task scheduling (round-robin)
    - Event processing
    - Future management
    - Step loop execution

    Subclasses must implement:
    - handle_io: Execute I/O operations
    - handle_await: Await external awaitables
    - handle_time_wait: Handle time-based waiting
    - get_current_time: Get current time (real or simulated)
    """

    def __init__(self, handlers: HandlerRegistry) -> None:
        """Initialize runtime with handler registry.

        Args:
            handlers: Handler registry mapping effect types to handlers
        """
        self.handlers = handlers

    def run(
        self,
        program: Program,
        env: dict[Any, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> Any:
        """Run a program to completion.

        Args:
            program: The program to execute
            env: Optional initial environment
            store: Optional initial store

        Returns:
            The final result value

        Raises:
            Exception if the program fails
        """
        from doeff._vendor import FrozenDict

        # Create initial state
        state = CESKState.initial(
            program=program,
            env=FrozenDict(env) if env else FrozenDict(),
            store=store or {},
        )

        # Run until done
        final_state = self.run_to_completion(state)

        # Get result from root task
        root_task = final_state.tasks.get(TaskId(0))
        if root_task is None:
            raise RuntimeError("Root task not found in final state")

        if root_task.status == TaskStatus.DONE:
            if isinstance(root_task.C, Value):
                return root_task.C.v
            raise RuntimeError("Task done but no value")

        if root_task.status == TaskStatus.FAILED:
            if isinstance(root_task.C, Error):
                raise root_task.C.ex
            raise RuntimeError("Task failed but no error")

        raise RuntimeError(f"Unexpected task status: {root_task.status}")

    def run_to_completion(self, state: CESKState) -> CESKState:
        """Run all tasks until completion.

        This is the main execution loop. Subclasses can override for
        async execution or simulation.

        Args:
            state: Initial CESK state

        Returns:
            Final state after all tasks complete
        """
        while True:
            # Get runnable tasks
            runnable = self._get_runnable_tasks(state)
            if not runnable:
                # No runnable tasks - check if done
                if self._is_all_done(state):
                    return state
                # Tasks blocked - let subclass handle
                state = self._handle_blocked_state(state)
                continue

            # Step the first runnable task
            task_id = runnable[0]
            output = step(state, task_id, self.handlers)

            # Process events
            state = self._process_events(output)

    def step_once(self, state: CESKState, task_id: TaskId) -> StepOutput:
        """Execute one step for a specific task.

        Args:
            state: Current CESK state
            task_id: Task to step

        Returns:
            StepOutput with new state and events
        """
        return step(state, task_id, self.handlers)

    def _get_runnable_tasks(self, state: CESKState) -> list[TaskId]:
        """Get list of tasks that can be stepped."""
        return [
            task_id
            for task_id, task in state.tasks.items()
            if task.status == TaskStatus.RUNNING
        ]

    def _is_all_done(self, state: CESKState) -> bool:
        """Check if all tasks are in terminal states."""
        return all(
            task.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)
            for task in state.tasks.values()
        )

    def _handle_blocked_state(self, state: CESKState) -> CESKState:
        """Handle state where no tasks are runnable.

        Default implementation just returns state unchanged.
        Subclasses should override to handle I/O, time waits, etc.
        """
        return state

    def _process_events(self, output: StepOutput) -> CESKState:
        """Process events emitted by step and return updated state.

        Args:
            output: StepOutput containing state and events

        Returns:
            Updated state after processing events
        """
        state = output.state

        for event in output.events:
            state = self._process_event(state, event)

        return state

    def _process_event(self, state: CESKState, event: Event) -> CESKState:
        """Process a single event.

        Args:
            state: Current state
            event: Event to process

        Returns:
            Updated state
        """
        if isinstance(event, TaskDone):
            # Task completed - nothing extra to do (step already updated state)
            return state

        if isinstance(event, TaskFailed):
            # Task failed - nothing extra to do
            return state

        if isinstance(event, TaskCancelled):
            # Task cancelled - nothing extra to do
            return state

        if isinstance(event, TaskCreated):
            # New task created - already added by step
            return state

        if isinstance(event, TasksCreated):
            # Multiple tasks created - already added by step
            return state

        if isinstance(event, TaskReady):
            # Task ready to run - update status
            task = state.tasks.get(event.task_id)
            if task is not None and task.status == TaskStatus.WAITING:
                # Wake up the task - need to resume it with future value
                state = self._wake_waiting_task(state, event.task_id)
            return state

        if isinstance(event, TaskWaitingOnFuture):
            # Task waiting - check if future already resolved
            future = state.futures.get(event.future_id)
            if future is not None and future.is_done:
                # Future already done - wake task immediately
                state = self._wake_with_future(state, event.task_id, future)
            return state

        if isinstance(event, TaskWaitingOnFutures):
            # Gather wait - check if all done
            return self._check_gather_complete(state, event.task_id, event.future_ids)

        if isinstance(event, TaskRacing):
            # Race wait - check if any done
            return self._check_race_complete(state, event.task_id, event.future_ids)

        if isinstance(event, TaskWaitingUntilTime):
            # Time wait - subclass handles
            return self._handle_time_wait(state, event.task_id, event.target_time)

        if isinstance(event, TaskWaitingForDuration):
            # Duration wait - subclass handles
            return self._handle_duration_wait(state, event.task_id, event.seconds)

        if isinstance(event, IORequested):
            # I/O needed - subclass handles
            return self._handle_io_request(state, event.task_id, event.operation)

        if isinstance(event, AwaitRequested):
            # Await needed - subclass handles
            return self._handle_await_request(state, event.task_id, event.awaitable)

        if isinstance(event, FutureResolved):
            # Future resolved - wake waiters
            return self._wake_future_waiters(state, event.future_id, event.value, None)

        if isinstance(event, FutureRejected):
            # Future rejected - wake waiters with error
            return self._wake_future_waiters(state, event.future_id, None, event.error)

        # Unknown event - ignore
        return state

    def _wake_waiting_task(self, state: CESKState, task_id: TaskId) -> CESKState:
        """Wake a waiting task by resolving its condition."""
        task = state.tasks.get(task_id)
        if task is None:
            return state

        condition = task.condition
        if condition is None:
            return state

        if isinstance(condition, WaitingOn):
            future = state.futures.get(condition.future_id)
            if future is not None and future.is_done:
                return self._wake_with_future(state, task_id, future)

        # Other conditions handled elsewhere
        return state

    def _wake_with_future(self, state: CESKState, task_id: TaskId, future: FutureState) -> CESKState:
        """Wake a task with a resolved future value."""
        if future.error is not None:
            return resume_task_error(state, task_id, future.error)
        return resume_task(state, task_id, future.value)

    def _wake_future_waiters(
        self,
        state: CESKState,
        future_id: FutureId,
        value: Any | None,
        error: BaseException | None,
    ) -> CESKState:
        """Wake all tasks waiting on a future."""
        future = state.futures.get(future_id)
        if future is None:
            return state

        for waiter_id in future.waiters:
            task = state.tasks.get(waiter_id)
            if task is None or task.status != TaskStatus.WAITING:
                continue

            condition = task.condition
            if isinstance(condition, WaitingOn) and condition.future_id == future_id:
                # Simple wait - wake immediately
                if error is not None:
                    state = resume_task_error(state, waiter_id, error)
                else:
                    state = resume_task(state, waiter_id, value)

            elif isinstance(condition, GatherCondition):
                # Gather - check if all done
                state = self._check_gather_complete(state, waiter_id, condition.future_ids)

            elif isinstance(condition, RaceCondition):
                # Race - first completion wins
                state = self._check_race_complete(state, waiter_id, condition.future_ids)

        return state

    def _check_gather_complete(
        self,
        state: CESKState,
        task_id: TaskId,
        future_ids: tuple[FutureId, ...],
    ) -> CESKState:
        """Check if all futures in a gather are complete."""
        results = []
        first_error = None

        for fid in future_ids:
            future = state.futures.get(fid)
            if future is None or not future.is_done:
                # Not all done yet
                return state
            if future.error is not None and first_error is None:
                first_error = future.error
            results.append(future.value if future.error is None else None)

        # All done - wake task
        if first_error is not None:
            return resume_task_error(state, task_id, first_error)
        return resume_task(state, task_id, results)

    def _check_race_complete(
        self,
        state: CESKState,
        task_id: TaskId,
        future_ids: tuple[FutureId, ...],
    ) -> CESKState:
        """Check if any future in a race is complete."""
        for fid in future_ids:
            future = state.futures.get(fid)
            if future is not None and future.is_done:
                # First completion wins
                if future.error is not None:
                    return resume_task_error(state, task_id, future.error)
                return resume_task(state, task_id, future.value)

        # None done yet
        return state

    # ========================================================================
    # Abstract methods for subclasses to implement
    # ========================================================================

    def _handle_time_wait(
        self, state: CESKState, task_id: TaskId, target_time: float
    ) -> CESKState:
        """Handle a task waiting until a specific time.

        Default implementation returns state unchanged.
        Subclasses should override for time simulation or real waiting.
        """
        return state

    def _handle_duration_wait(
        self, state: CESKState, task_id: TaskId, seconds: float
    ) -> CESKState:
        """Handle a task waiting for a duration.

        Default implementation returns state unchanged.
        Subclasses should override for time simulation or real waiting.
        """
        return state

    def _handle_io_request(
        self, state: CESKState, task_id: TaskId, operation: Any
    ) -> CESKState:
        """Handle an I/O request from a task.

        Default implementation returns state unchanged.
        Subclasses should override to execute I/O.
        """
        return state

    def _handle_await_request(
        self, state: CESKState, task_id: TaskId, awaitable: Any
    ) -> CESKState:
        """Handle an await request from a task.

        Default implementation returns state unchanged.
        Subclasses should override for async execution.
        """
        return state


__all__ = [
    "BaseRuntime",
]
