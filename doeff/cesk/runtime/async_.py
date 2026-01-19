"""AsyncRuntime - Reference implementation for the doeff effect system.

This runtime implements parallel Gather execution via asyncio and serves as
the canonical reference for runtime behavior per SPEC-CESK-001.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar

from doeff._vendor import Err, Ok
from doeff.cesk.runtime.base import BaseRuntime
from doeff.cesk.state import CESKState, TaskState, ProgramControl, Done as TaskDoneStatus
from doeff.cesk.result import Done, Failed, Suspended
from doeff.cesk.step import step
from doeff.cesk.handlers import Handler, default_handlers
from doeff.cesk.frames import ContinueValue, ContinueError, ContinueProgram, FrameResult
from doeff.cesk.types import Store, TaskId
from doeff.cesk.runtime_result import RuntimeResult
from doeff.effects.future import FutureAwaitEffect
from doeff.effects.gather import GatherEffect
from doeff.effects.time import DelayEffect, WaitUntilEffect

if TYPE_CHECKING:
    from doeff.program import Program

T = TypeVar("T")


def _placeholder_handler(effect: Any, task_state: TaskState, store: Store) -> ContinueValue:
    """Placeholder handler for effects that are intercepted by the runtime."""
    return ContinueValue(
        value=None,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


class AsyncRuntime(BaseRuntime):
    """Asynchronous runtime with parallel Gather support.

    This is the reference implementation for the doeff effect system.
    It implements:
    - All core effects (Ask, Get, Put, Modify, Tell, Listen)
    - Control flow effects (Local, Safe, Intercept)
    - Parallel Gather execution via asyncio
    - Async time effects (Delay, WaitUntil)
    - External awaitable integration (Await)

    Per SPEC-CESK-001, this runtime:
    - Uses step.py for pure state transitions
    - Dispatches effects to handlers
    - Intercepts Gather, Await, Delay, WaitUntil for async handling
    """

    def __init__(self, handlers: dict[type, Handler] | None = None):
        """Initialize AsyncRuntime with optional custom handlers.

        Args:
            handlers: Optional dict mapping effect types to custom handlers.
                     These override the default handlers.
        """
        base_handlers = default_handlers()
        # Register placeholders for effects we intercept
        base_handlers[DelayEffect] = _placeholder_handler
        base_handlers[WaitUntilEffect] = _placeholder_handler
        base_handlers[FutureAwaitEffect] = _placeholder_handler
        base_handlers[GatherEffect] = _placeholder_handler

        if handlers:
            base_handlers.update(handlers)

        super().__init__(base_handlers)
        self._user_handlers = handlers or {}

    async def run(
        self,
        program: Program[T],
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> RuntimeResult[T]:
        """Execute a program and return RuntimeResult.

        Args:
            program: The program to execute
            env: Optional initial environment (reader context)
            store: Optional initial store (mutable state)

        Returns:
            RuntimeResult containing the outcome and debugging context
        """
        initial_env = dict(env) if env else {}
        initial_store = dict(store) if store else {}
        state = self._create_initial_state(program, env, store)

        try:
            final_value, final_state = await self._run_scheduler(state)
            return self._build_success_result(final_value, final_state)
        except asyncio.CancelledError:
            # Let cancellation propagate - this is external control, not a program error
            raise
        except Exception as exc:
            # Program errors get wrapped in RuntimeResult
            return self._build_error_result(exc, state)

    async def run_and_unwrap(
        self,
        program: Program[T],
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> T:
        """Execute a program and return just the value (raises on error).

        This is a convenience method for when you don't need the full
        RuntimeResult context. Equivalent to `(await run(...)).value`.

        Args:
            program: The program to execute
            env: Optional initial environment
            store: Optional initial store

        Returns:
            The program's return value

        Raises:
            Any exception raised during program execution
        """
        result = await self.run(program, env, store)
        return result.value

    async def _run_scheduler(self, state: CESKState) -> tuple[Any, CESKState]:
        """Run the scheduler loop until completion.

        Returns:
            Tuple of (final_value, final_state)

        Raises:
            Any exception from the main task
        """
        pending_async: dict[TaskId, tuple[asyncio.Task[Any], Suspended]] = {}
        task_results: dict[TaskId, Any] = {}
        task_errors: dict[TaskId, BaseException] = {}
        gather_waiters: dict[TaskId, tuple[list[TaskId], Suspended]] = {}

        main_task_id = state.main_task

        while True:
            ready_task_ids = [
                tid for tid in state.get_ready_tasks()
                if tid not in pending_async and tid not in gather_waiters
            ]

            if ready_task_ids:
                task_id = ready_task_ids[0]
                single_state = self._make_single_task_state(state, task_id)
                result = step(single_state, self._handlers)

                if isinstance(result, Done):
                    state = self._update_store(state, result.store)
                    if task_id == main_task_id:
                        await self._cancel_all(pending_async)
                        return (result.value, state)
                    # Mark child task as Done
                    done_task = state.tasks[task_id].with_status(TaskDoneStatus.ok(result.value))
                    state = state.with_task(task_id, done_task)
                    task_results[task_id] = result.value
                    state = self._check_gather_complete(
                        state, task_id, gather_waiters, task_results, task_errors
                    )
                    continue

                if isinstance(result, Failed):
                    state = self._update_store(state, result.store)
                    if task_id == main_task_id:
                        await self._cancel_all(pending_async)
                        exc = result.exception
                        if result.captured_traceback is not None:
                            exc.__cesk_traceback__ = result.captured_traceback  # type: ignore[attr-defined]
                        raise exc
                    # Mark child task as Failed
                    failed_task = state.tasks[task_id].with_status(
                        TaskDoneStatus(Err(result.exception))  # type: ignore[arg-type]
                    )
                    state = state.with_task(task_id, failed_task)
                    task_errors[task_id] = result.exception
                    state = self._check_gather_complete(
                        state, task_id, gather_waiters, task_results, task_errors
                    )
                    continue

                if isinstance(result, CESKState):
                    state = self._merge_task(state, task_id, result)
                    continue

                if isinstance(result, Suspended):
                    effect = result.effect
                    effect_type = type(effect)

                    # User handlers take priority
                    if effect_type in self._user_handlers:
                        task_state = state.tasks[task_id]
                        dispatch_result = self._dispatch_effect(effect, task_state, state.store)
                        state = self._apply_dispatch_result(state, task_id, result, dispatch_result)
                        continue

                    # Runtime intercepts Gather for parallel execution
                    if isinstance(effect, GatherEffect):
                        programs = effect.programs
                        if not programs:
                            new_single = result.resume([], state.store)
                            state = self._merge_task(state, task_id, new_single)
                            continue

                        child_ids: list[TaskId] = []
                        current_env = state.tasks[task_id].env
                        for prog in programs:
                            child_id = TaskId.new()
                            from doeff.program import Program
                            child_task = TaskState.initial(prog, dict(current_env))  # type: ignore[arg-type]
                            state = state.add_task(child_id, child_task)
                            child_ids.append(child_id)

                        gather_waiters[task_id] = (child_ids, result)
                        continue

                    # Runtime intercepts Await for asyncio integration
                    if isinstance(effect, FutureAwaitEffect):
                        coro = self._do_await(effect.awaitable)
                        pending_async[task_id] = (asyncio.create_task(coro), result)
                        continue

                    # Runtime intercepts Delay for async sleep
                    if isinstance(effect, DelayEffect):
                        coro = self._do_delay(effect.seconds)
                        pending_async[task_id] = (asyncio.create_task(coro), result)
                        continue

                    # Runtime intercepts WaitUntil for async sleep until
                    if isinstance(effect, WaitUntilEffect):
                        coro = self._do_wait_until(effect.target_time)
                        pending_async[task_id] = (asyncio.create_task(coro), result)
                        continue

                    # Default: dispatch to handler
                    task_state = state.tasks[task_id]
                    dispatch_result = self._dispatch_effect(effect, task_state, state.store)
                    state = self._apply_dispatch_result(state, task_id, result, dispatch_result)
                    continue

            # No ready tasks - wait for async operations
            if pending_async:
                tasks_only = [t for t, _ in pending_async.values()]
                done, _ = await asyncio.wait(tasks_only, return_when=asyncio.FIRST_COMPLETED)

                for tid in list(pending_async.keys()):
                    atask, suspended = pending_async[tid]
                    if atask in done:
                        del pending_async[tid]
                        try:
                            value = atask.result()
                            new_single = suspended.resume(value, state.store)
                            state = self._merge_task(state, tid, new_single)
                        except Exception as ex:
                            error_state = suspended.resume_error(ex)
                            error_state = self._fix_store_rollback(error_state, state.store)
                            state = self._merge_task(state, tid, error_state)
                        break
                continue

            # Check if main task completed
            if not ready_task_ids and not pending_async:
                if state.is_main_task_done():
                    main_result = state.get_main_result()
                    if main_result is not None:
                        if main_result.is_ok():
                            return (main_result.ok(), state)
                        raise main_result.err()  # type: ignore[misc]
                await asyncio.sleep(0)

    async def _do_await(self, awaitable: Any) -> Any:
        """Await an external coroutine."""
        return await awaitable

    async def _do_delay(self, seconds: float) -> None:
        """Sleep for the specified duration."""
        await asyncio.sleep(seconds)

    async def _do_wait_until(self, target_time: datetime) -> None:
        """Sleep until the target time."""
        now = datetime.now()
        if target_time > now:
            delay_seconds = (target_time - now).total_seconds()
            await asyncio.sleep(delay_seconds)

    def _make_single_task_state(self, state: CESKState, task_id: TaskId) -> CESKState:
        """Create a single-task CESKState for stepping."""
        task = state.tasks[task_id]
        return CESKState(
            tasks={task_id: task},
            store=state.store,
            main_task=task_id,
            futures=state.futures,
            spawn_results=state.spawn_results,
        )

    def _merge_task(self, state: CESKState, task_id: TaskId, stepped: CESKState) -> CESKState:
        """Merge stepped task state back into multi-task state."""
        new_tasks = dict(state.tasks)
        new_tasks[task_id] = stepped.tasks[stepped.main_task]
        return CESKState(
            tasks=new_tasks,
            store=stepped.store,
            main_task=state.main_task,
            futures=state.futures,
            spawn_results=state.spawn_results,
        )

    def _fix_store_rollback(self, error_state: CESKState, current_store: Store) -> CESKState:
        """Fix store in error state to use current store (no rollback)."""
        return CESKState(
            tasks={error_state.main_task: error_state.tasks[error_state.main_task]},
            store=current_store,
            main_task=error_state.main_task,
            futures=error_state.futures,
            spawn_results=error_state.spawn_results,
        )

    def _update_store(self, state: CESKState, store: Store) -> CESKState:
        """Update store in state."""
        return CESKState(
            tasks=state.tasks,
            store=store,
            main_task=state.main_task,
            futures=state.futures,
            spawn_results=state.spawn_results,
        )

    def _apply_dispatch_result(
        self,
        state: CESKState,
        task_id: TaskId,
        suspended: Suspended,
        dispatch_result: FrameResult,
    ) -> CESKState:
        """Apply handler dispatch result to state."""
        if isinstance(dispatch_result, ContinueError):
            new_single = suspended.resume_error(dispatch_result.error)
            return self._merge_task(state, task_id, new_single)
        if isinstance(dispatch_result, ContinueProgram):
            return self._merge_task(state, task_id, CESKState(
                C=ProgramControl(dispatch_result.program),
                E=dispatch_result.env,
                S=dispatch_result.store,
                K=dispatch_result.k,
            ))
        if isinstance(dispatch_result, ContinueValue):
            new_single = suspended.resume(dispatch_result.value, dispatch_result.store)
            return self._merge_task(state, task_id, new_single)
        raise RuntimeError(f"Unexpected dispatch result type: {type(dispatch_result)}")

    def _check_gather_complete(
        self,
        state: CESKState,
        completed_id: TaskId,
        gather_waiters: dict[TaskId, tuple[list[TaskId], Suspended]],
        task_results: dict[TaskId, Any],
        task_errors: dict[TaskId, BaseException],
    ) -> CESKState:
        """Check if any Gather is complete after a child finishes."""
        for parent_id, (child_ids, suspended) in list(gather_waiters.items()):
            if completed_id not in child_ids:
                continue

            # If child failed, fail the Gather immediately (fail-fast)
            if completed_id in task_errors:
                del gather_waiters[parent_id]
                error_state = suspended.resume_error(task_errors[completed_id])
                error_state = self._fix_store_rollback(error_state, state.store)
                return self._merge_task(state, parent_id, error_state)

            # Check if all children are done
            all_done = all(cid in task_results or cid in task_errors for cid in child_ids)
            if all_done:
                del gather_waiters[parent_id]
                results = [task_results[cid] for cid in child_ids]
                new_single = suspended.resume(results, state.store)
                return self._merge_task(state, parent_id, new_single)

        return state

    async def _cancel_all(
        self,
        pending: dict[TaskId, tuple[asyncio.Task[Any], Suspended]],
    ) -> None:
        """Cancel all pending async tasks."""
        for atask, _ in pending.values():
            atask.cancel()
        if pending:
            tasks = [t for t, _ in pending.values()]
            await asyncio.gather(*tasks, return_exceptions=True)


__all__ = [
    "AsyncRuntime",
]
