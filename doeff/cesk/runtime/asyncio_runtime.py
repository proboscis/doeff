"""Asyncio runtime for async execution.

AsyncioRuntime provides:
- Non-blocking I/O with asyncio
- Real time waiting with asyncio.sleep
- Concurrent task execution
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from doeff.cesk.runtime.base import BaseRuntime
from doeff.cesk.state import CESKState, TaskStatus
from doeff.cesk.types import TaskId
from doeff.cesk.step import resume_task, resume_task_error

if TYPE_CHECKING:
    from doeff.cesk.handlers import HandlerRegistry


class AsyncioRuntime(BaseRuntime):
    """Asyncio runtime for non-blocking execution.

    Executes programs with asyncio:
    - I/O operations are awaited
    - Time waits use asyncio.sleep()
    - Supports concurrent task execution

    Example:
        runtime = AsyncioRuntime(handlers)
        result = await runtime.run_async(my_program)

        # Or run synchronously
        result = runtime.run(my_program)
    """

    def __init__(self, handlers: HandlerRegistry) -> None:
        """Initialize asyncio runtime.

        Args:
            handlers: Handler registry
        """
        super().__init__(handlers)
        self._pending_awaits: dict[TaskId, Any] = {}

    def run(
        self,
        program: Any,
        env: dict[Any, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> Any:
        """Run a program synchronously using asyncio.run().

        Args:
            program: The program to execute
            env: Optional initial environment
            store: Optional initial store

        Returns:
            The final result value
        """
        return asyncio.run(self.run_async(program, env, store))

    async def run_async(
        self,
        program: Any,
        env: dict[Any, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> Any:
        """Run a program asynchronously.

        Args:
            program: The program to execute
            env: Optional initial environment
            store: Optional initial store

        Returns:
            The final result value
        """
        from doeff._vendor import FrozenDict
        from doeff.cesk.state import CESKState, Error, Value

        # Create initial state
        state = CESKState.initial(
            program=program,
            env=FrozenDict(env) if env else FrozenDict(),
            store=store or {},
        )

        # Run until done
        final_state = await self._run_to_completion_async(state)

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

    async def _run_to_completion_async(self, state: CESKState) -> CESKState:
        """Run all tasks asynchronously."""
        while True:
            # Get runnable tasks
            runnable = self._get_runnable_tasks(state)

            if runnable:
                # Step the first runnable task
                task_id = runnable[0]
                output = self.step_once(state, task_id)
                state = await self._process_events_async(output)
                continue

            # No runnable tasks
            if self._is_all_done(state):
                return state

            # Handle pending awaits
            if self._pending_awaits:
                state = await self._execute_pending_await(state)
                continue

            # All tasks blocked with nothing to do
            break

        return state

    async def _process_events_async(self, output: Any) -> CESKState:
        """Process events asynchronously."""
        state = output.state

        for event in output.events:
            state = await self._process_event_async(state, event)

        return state

    async def _process_event_async(self, state: CESKState, event: Any) -> CESKState:
        """Process a single event asynchronously."""
        from doeff.cesk.events import (
            AwaitRequested,
            IORequested,
            TaskWaitingForDuration,
            TaskWaitingUntilTime,
        )

        if isinstance(event, TaskWaitingUntilTime):
            return await self._handle_time_wait_async(state, event.task_id, event.target_time)

        if isinstance(event, TaskWaitingForDuration):
            return await self._handle_duration_wait_async(state, event.task_id, event.seconds)

        if isinstance(event, IORequested):
            return await self._handle_io_request_async(state, event.task_id, event.operation)

        if isinstance(event, AwaitRequested):
            return await self._handle_await_request_async(state, event.task_id, event.awaitable)

        # Use base implementation for other events
        return self._process_event(state, event)

    async def _execute_pending_await(self, state: CESKState) -> CESKState:
        """Execute a pending await."""
        if not self._pending_awaits:
            return state

        task_id, awaitable = next(iter(self._pending_awaits.items()))
        del self._pending_awaits[task_id]

        try:
            result = await awaitable
            return resume_task(state, task_id, result)
        except Exception as ex:
            return resume_task_error(state, task_id, ex)

    async def _handle_time_wait_async(
        self, state: CESKState, task_id: TaskId, target_time: float
    ) -> CESKState:
        """Handle wait until specific time asynchronously."""
        import time

        current = time.time()
        if target_time > current:
            wait_seconds = target_time - current
            await asyncio.sleep(wait_seconds)
        return resume_task(state, task_id, None)

    async def _handle_duration_wait_async(
        self, state: CESKState, task_id: TaskId, seconds: float
    ) -> CESKState:
        """Handle wait for duration asynchronously."""
        if seconds > 0:
            await asyncio.sleep(seconds)
        return resume_task(state, task_id, None)

    async def _handle_io_request_async(
        self, state: CESKState, task_id: TaskId, operation: Any
    ) -> CESKState:
        """Handle I/O request asynchronously."""
        try:
            if asyncio.iscoroutinefunction(operation):
                result = await operation()
            elif callable(operation):
                # Run sync operation in executor
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, operation)
            else:
                raise TypeError(f"I/O operation must be callable, got {type(operation)}")
            return resume_task(state, task_id, result)
        except Exception as ex:
            return resume_task_error(state, task_id, ex)

    async def _handle_await_request_async(
        self, state: CESKState, task_id: TaskId, awaitable: Any
    ) -> CESKState:
        """Handle await request asynchronously."""
        try:
            result = await awaitable
            return resume_task(state, task_id, result)
        except Exception as ex:
            return resume_task_error(state, task_id, ex)

    # Sync versions just mark as pending
    def _handle_time_wait(
        self, state: CESKState, task_id: TaskId, target_time: float
    ) -> CESKState:
        """Mark time wait as pending."""
        return state

    def _handle_duration_wait(
        self, state: CESKState, task_id: TaskId, seconds: float
    ) -> CESKState:
        """Mark duration wait as pending."""
        return state

    def _handle_io_request(
        self, state: CESKState, task_id: TaskId, operation: Any
    ) -> CESKState:
        """Mark I/O as pending."""
        return state

    def _handle_await_request(
        self, state: CESKState, task_id: TaskId, awaitable: Any
    ) -> CESKState:
        """Mark await as pending."""
        self._pending_awaits[task_id] = awaitable
        return state


__all__ = [
    "AsyncioRuntime",
]
