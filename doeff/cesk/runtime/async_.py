"""AsyncRuntime - Reference implementation for the doeff effect system.

This runtime implements parallel Gather execution via asyncio and serves as
the canonical reference for runtime behavior per SPEC-CESK-001.

Spawn Support (SPEC-EFF-005):
- SpawnEffect: Creates a background task with snapshot semantics
- WaitEffect: Waits for Future completion
- TaskCancelEffect: Requests task cancellation
- TaskIsDoneEffect: Checks completion status
- CreatePromiseEffect/CompletePromiseEffect/FailPromiseEffect: User-level promises
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import uuid4

from doeff._vendor import Err
from doeff.cesk.frames import ContinueError, ContinueProgram, ContinueValue, FrameResult
from doeff.cesk.handlers import Handler, default_handlers
from doeff.cesk.result import Done, Failed, Suspended
from doeff.cesk.runtime.base import BaseRuntime
from doeff.cesk.runtime_result import RuntimeResult
from doeff.cesk.state import CESKState, ProgramControl, TaskState
from doeff.cesk.state import Done as TaskDoneStatus
from doeff.cesk.step import step
from doeff.cesk.types import Store, TaskId
from doeff.effects.future import FutureAwaitEffect
from doeff.effects.gather import GatherEffect
from doeff.effects.race import RaceEffect, RaceResult
from doeff.effects.spawn import (
    Future,
    Promise,
    SpawnEffect,
    Task,
    TaskCancelEffect,
    TaskCancelledError,
    TaskIsDoneEffect,
)
from doeff.effects.promise import (
    CreatePromiseEffect,
    CompletePromiseEffect,
    FailPromiseEffect,
)
from doeff.effects.wait import WaitEffect
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


@dataclass
class SpawnedTaskInfo:
    """Information about a spawned task.
    
    Tracks the internal TaskId, completion status, result/error, and cancellation state.
    """
    task_id: TaskId
    env_snapshot: dict[Any, Any]
    store_snapshot: dict[str, Any]
    is_cancelled: bool = False
    result: Any = None
    error: BaseException | None = None
    is_complete: bool = False


class AsyncRuntime(BaseRuntime):
    """Asynchronous runtime with parallel Gather and Spawn support.

    This is the reference implementation for the doeff effect system.
    It implements:
    - All core effects (Ask, Get, Put, Modify, Tell, Listen)
    - Control flow effects (Local, Safe, Intercept)
    - Parallel Gather execution via asyncio
    - Background Spawn/Task execution (SPEC-EFF-005)
    - Async time effects (Delay, WaitUntil)
    - External awaitable integration (Await)

    Per SPEC-CESK-001, this runtime:
    - Uses step.py for pure state transitions
    - Dispatches effects to handlers
    - Intercepts Gather, Await, Delay, WaitUntil, Spawn for async handling
    """


    def _get_store_for_task(
        self,
        task_id: TaskId,
        state: CESKState,
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        task_id_to_handle: dict[TaskId, Any],
    ) -> Store:
        """Get the appropriate store for a task (isolated for spawned, shared otherwise)."""
        if task_id in task_id_to_handle:
            handle_id = task_id_to_handle[task_id]
            return spawned_tasks[handle_id].store_snapshot
        return state.store

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
        base_handlers[RaceEffect] = _placeholder_handler
        # Spawn effects are intercepted by runtime
        base_handlers[SpawnEffect] = _placeholder_handler
        base_handlers[TaskCancelEffect] = _placeholder_handler
        base_handlers[TaskIsDoneEffect] = _placeholder_handler
        base_handlers[WaitEffect] = _placeholder_handler
        base_handlers[CreatePromiseEffect] = _placeholder_handler
        base_handlers[CompletePromiseEffect] = _placeholder_handler
        base_handlers[FailPromiseEffect] = _placeholder_handler

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
            final_store = getattr(exc, "__cesk_store__", None)
            return self._build_error_result(exc, state, final_store=final_store)

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
        gather_task_meta: dict[TaskId, tuple[tuple[Future[Any], ...], dict[TaskId, int]]] = {}
        race_waiters: dict[TaskId, tuple[list[TaskId], Suspended]] = {}
        race_task_meta: dict[TaskId, tuple[tuple[Future[Any], ...], dict[TaskId, Future[Any]]]] = {}

        # Spawn tracking: maps Task handle ID -> SpawnedTaskInfo
        spawned_tasks: dict[Any, SpawnedTaskInfo] = {}
        # Maps internal TaskId -> Task handle ID for reverse lookup
        task_id_to_handle: dict[TaskId, Any] = {}
        # Join waiters: maps Task/Promise handle ID -> list of (parent TaskId, Suspended)
        join_waiters: dict[Any, list[tuple[TaskId, Suspended]]] = {}
        # Promise tracking: maps Promise handle ID -> Promise object
        user_promises: dict[Any, Promise[Any]] = {}

        main_task_id = state.main_task

        while True:
            # Collect all task IDs that are waiting on joins
            join_waiting_task_ids = {
                parent_tid
                for waiters in join_waiters.values()
                for parent_tid, _ in waiters
            }

            ready_task_ids = [
                tid for tid in state.get_ready_tasks()
                if (tid not in pending_async
                    and tid not in gather_waiters
                    and tid not in race_waiters
                    and tid not in join_waiting_task_ids)
            ]

            if ready_task_ids:
                task_id = ready_task_ids[0]

                # Check if this is a spawned task - use isolated store if so
                isolated_store = None
                if task_id in task_id_to_handle:
                    handle_id = task_id_to_handle[task_id]
                    spawned_info = spawned_tasks[handle_id]
                    isolated_store = spawned_info.store_snapshot

                single_state = self._make_single_task_state(state, task_id, isolated_store)
                result = step(single_state, self._handlers)

                if isinstance(result, Done):
                    # Check if this is a spawned task (isolated store, don't merge to parent)
                    is_spawned_task = task_id in task_id_to_handle

                    if not is_spawned_task:
                        # Regular (Gather) task - merge store changes to parent
                        state = self._update_store(state, result.store)

                    if task_id == main_task_id:
                        await self._cancel_all(pending_async)
                        return (result.value, state)

                    # Mark child task as Done
                    done_task = state.tasks[task_id].with_status(TaskDoneStatus.ok(result.value))
                    state = state.with_task(task_id, done_task)
                    task_results[task_id] = result.value

                    # Check if this is a spawned task
                    if is_spawned_task:
                        handle_id = task_id_to_handle[task_id]
                        spawned_info = spawned_tasks[handle_id]
                        spawned_info.is_complete = True
                        spawned_info.result = result.value
                        # Update the spawned task's isolated store (for potential future use)
                        spawned_info.store_snapshot = result.store
                        # Resume any waiters
                        state = self._resume_join_waiters(
                            state, handle_id, join_waiters, spawned_tasks
                        )

                    state = self._check_gather_complete(
                        state, task_id, gather_waiters, gather_task_meta,
                        spawned_tasks, task_id_to_handle
                    )
                    state = self._check_race_complete(
                        state, task_id, race_waiters, task_results, task_errors, race_task_meta
                    )
                    continue

                if isinstance(result, Failed):
                    is_spawned_task = task_id in task_id_to_handle

                    if not is_spawned_task:
                        state = self._update_store(state, result.store)

                    if task_id == main_task_id:
                        await self._cancel_all(pending_async)
                        exc = result.exception
                        if result.captured_traceback is not None:
                            exc.__cesk_traceback__ = result.captured_traceback  # type: ignore[attr-defined]
                        exc.__cesk_store__ = result.store  # type: ignore[attr-defined]
                        raise exc
                    failed_task = state.tasks[task_id].with_status(
                        TaskDoneStatus(Err(result.exception))  # type: ignore[arg-type]
                    )
                    state = state.with_task(task_id, failed_task)
                    task_errors[task_id] = result.exception

                    if is_spawned_task:
                        handle_id = task_id_to_handle[task_id]
                        spawned_info = spawned_tasks[handle_id]
                        spawned_info.is_complete = True
                        spawned_info.error = result.exception
                        spawned_info.store_snapshot = result.store
                        state = self._resume_join_waiters(
                            state, handle_id, join_waiters, spawned_tasks
                        )

                    state = self._check_gather_complete(
                        state, task_id, gather_waiters, gather_task_meta,
                        spawned_tasks, task_id_to_handle
                    )
                    state = self._check_race_complete(
                        state, task_id, race_waiters, task_results, task_errors, race_task_meta
                    )
                    continue

                if isinstance(result, CESKState):
                    is_spawned_task = task_id in task_id_to_handle

                    if is_spawned_task:
                        handle_id = task_id_to_handle[task_id]
                        spawned_info = spawned_tasks[handle_id]
                        spawned_info.store_snapshot = result.store
                        # Only merge task state, not store
                        new_tasks = dict(state.tasks)
                        new_tasks[task_id] = result.tasks[result.main_task]
                        state = CESKState(
                            tasks=new_tasks,
                            store=state.store,  # Keep parent's store unchanged
                            main_task=state.main_task,
                            futures=state.futures,
                            spawn_results=state.spawn_results,
                        )
                    else:
                        state = self._merge_task(state, task_id, result)
                    continue

                if isinstance(result, Suspended):
                    effect = result.effect
                    effect_type = type(effect)

                    # User handlers take priority
                    if effect_type in self._user_handlers:
                        task_state = state.tasks[task_id]
                        # Use isolated store for spawned tasks
                        store_for_dispatch = self._get_store_for_task(
                            task_id, state, spawned_tasks, task_id_to_handle
                        )
                        dispatch_result = self._dispatch_effect(effect, task_state, store_for_dispatch)

                        # For spawned tasks, use isolated dispatch result handler
                        if task_id in task_id_to_handle:
                            handle_id = task_id_to_handle[task_id]
                            spawned_info = spawned_tasks[handle_id]
                            state = self._apply_dispatch_result_isolated(
                                state, task_id, result, dispatch_result, spawned_info
                            )
                        else:
                            state = self._apply_dispatch_result(state, task_id, result, dispatch_result)
                        continue

                    # Runtime intercepts SpawnEffect to create background task
                    if isinstance(effect, SpawnEffect):
                        state = self._handle_spawn(
                            state, task_id, effect, result,
                            spawned_tasks, task_id_to_handle
                        )
                        continue

                    # Runtime intercepts WaitEffect
                    if isinstance(effect, WaitEffect):
                        state = self._handle_wait(
                            state, task_id, effect, result,
                            spawned_tasks, join_waiters
                        )
                        continue

                    # Runtime intercepts TaskCancelEffect to request cancellation
                    if isinstance(effect, TaskCancelEffect):
                        state = self._handle_task_cancel(
                            state, task_id, effect, result,
                            spawned_tasks, join_waiters, pending_async
                        )
                        continue

                    # Runtime intercepts TaskIsDoneEffect to check completion
                    if isinstance(effect, TaskIsDoneEffect):
                        state = self._handle_task_is_done(
                            state, task_id, effect, result,
                            spawned_tasks
                        )
                        continue

                    if isinstance(effect, CreatePromiseEffect):
                        state = self._handle_create_promise(
                            state, task_id, result,
                            user_promises, spawned_tasks, task_id_to_handle
                        )
                        continue

                    if isinstance(effect, CompletePromiseEffect):
                        state = self._handle_complete_promise(
                            state, task_id, effect, result,
                            user_promises, join_waiters, spawned_tasks
                        )
                        continue

                    if isinstance(effect, FailPromiseEffect):
                        state = self._handle_fail_promise(
                            state, task_id, effect, result,
                            user_promises, join_waiters, spawned_tasks
                        )
                        continue

                    if isinstance(effect, GatherEffect):
                        futures = effect.futures
                        if not futures:
                            new_single = result.resume([], state.store)
                            state = self._merge_task(state, task_id, new_single)
                            continue

                        state = self._handle_gather_futures(
                            state, task_id, futures, result,
                            spawned_tasks, gather_waiters, gather_task_meta
                        )
                        continue

                    if isinstance(effect, RaceEffect):
                        futures = effect.futures
                        state = self._handle_race_futures(
                            state, task_id, futures, result,
                            spawned_tasks, race_waiters, race_task_meta
                        )
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

                    # Use isolated store for spawned tasks
                    if task_id in task_id_to_handle:
                        handle_id = task_id_to_handle[task_id]
                        spawned_info = spawned_tasks[handle_id]
                        store_for_dispatch = spawned_info.store_snapshot
                    else:
                        store_for_dispatch = state.store

                    dispatch_result = self._dispatch_effect(effect, task_state, store_for_dispatch)

                    # For spawned tasks, update isolated store instead of parent store
                    if task_id in task_id_to_handle:
                        handle_id = task_id_to_handle[task_id]
                        spawned_info = spawned_tasks[handle_id]
                        state = self._apply_dispatch_result_isolated(
                            state, task_id, result, dispatch_result, spawned_info
                        )
                    else:
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

                        # Check if this is a spawned task that was cancelled
                        if tid in task_id_to_handle:
                            handle_id = task_id_to_handle[tid]
                            spawned_info = spawned_tasks[handle_id]
                            if spawned_info.is_cancelled:
                                # Task was cancelled - ignore async completion
                                continue

                        # Get appropriate store for this task
                        store_for_resume = self._get_store_for_task(
                            tid, state, spawned_tasks, task_id_to_handle
                        )

                        try:
                            value = atask.result()
                            new_single = suspended.resume(value, store_for_resume)

                            # For spawned tasks, update isolated store
                            if tid in task_id_to_handle:
                                handle_id = task_id_to_handle[tid]
                                spawned_info = spawned_tasks[handle_id]
                                spawned_info.store_snapshot = new_single.store
                                # Only merge task state, keep parent's store
                                new_tasks = dict(state.tasks)
                                new_tasks[tid] = new_single.tasks[new_single.main_task]
                                state = CESKState(
                                    tasks=new_tasks,
                                    store=state.store,
                                    main_task=state.main_task,
                                    futures=state.futures,
                                    spawn_results=state.spawn_results,
                                )
                            else:
                                state = self._merge_task(state, tid, new_single)
                        except Exception as ex:
                            error_state = suspended.resume_error(ex)

                            # For spawned tasks, use isolated store
                            if tid in task_id_to_handle:
                                handle_id = task_id_to_handle[tid]
                                spawned_info = spawned_tasks[handle_id]
                                spawned_info.store_snapshot = store_for_resume
                                new_tasks = dict(state.tasks)
                                new_tasks[tid] = error_state.tasks[error_state.main_task]
                                state = CESKState(
                                    tasks=new_tasks,
                                    store=state.store,
                                    main_task=state.main_task,
                                    futures=state.futures,
                                    spawn_results=state.spawn_results,
                                )
                            else:
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

    def _handle_spawn(
        self,
        state: CESKState,
        task_id: TaskId,
        effect: SpawnEffect,
        suspended: Suspended,
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        task_id_to_handle: dict[TaskId, Any],
    ) -> CESKState:
        """Handle SpawnEffect by creating a new background task with snapshot semantics."""
        # Create unique handle ID
        handle_id = uuid4()

        # Get current task's env for the snapshot
        current_task = state.tasks[task_id]
        env_snapshot = dict(current_task.env)

        # Snapshot the store - if spawning from a spawned task, use its isolated store
        if task_id in task_id_to_handle:
            parent_handle_id = task_id_to_handle[task_id]
            parent_info = spawned_tasks[parent_handle_id]
            store_snapshot = {k: v for k, v in parent_info.store_snapshot.items()}
        else:
            store_snapshot = {k: v for k, v in state.store.items()}

        # Create child task with the snapshot (isolated store not used, but env is)
        child_id = TaskId.new()
        child_task = TaskState.initial(effect.program, env_snapshot)  # type: ignore[arg-type]
        state = state.add_task(child_id, child_task)

        # Determine backend (default to "thread" for asyncio-based runtime)
        backend = effect.preferred_backend or "thread"

        # Create the Task handle
        task_handle = Task(
            backend=backend,
            _handle=handle_id,
            _env_snapshot=env_snapshot,
            _state_snapshot=store_snapshot,
        )

        # Track the spawned task
        spawned_info = SpawnedTaskInfo(
            task_id=child_id,
            env_snapshot=env_snapshot,
            store_snapshot=store_snapshot,
        )
        spawned_tasks[handle_id] = spawned_info
        task_id_to_handle[child_id] = handle_id

        # Resume parent with the Task handle
        new_single = suspended.resume(task_handle, state.store)
        return self._merge_task(state, task_id, new_single)

    def _handle_task_cancel(
        self,
        state: CESKState,
        task_id: TaskId,
        effect: TaskCancelEffect,
        suspended: Suspended,
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        join_waiters: dict[Any, list[tuple[TaskId, Suspended]]],
        pending_async: dict[TaskId, tuple[asyncio.Task[Any], Suspended]] | None = None,
    ) -> CESKState:
        """Handle TaskCancelEffect by requesting task cancellation."""
        handle_id = effect.task._handle

        if handle_id not in spawned_tasks:
            # Task handle is invalid - return False
            new_single = suspended.resume(False, state.store)
            return self._merge_task(state, task_id, new_single)

        spawned_info = spawned_tasks[handle_id]

        # Check if task is already complete
        if spawned_info.is_complete:
            # Already done - cancellation has no effect
            new_single = suspended.resume(False, state.store)
            return self._merge_task(state, task_id, new_single)

        # Mark as cancelled
        spawned_info.is_cancelled = True
        spawned_info.is_complete = True
        spawned_info.error = TaskCancelledError()

        child_task_id = spawned_info.task_id

        # Cancel any pending async operations for this task
        if pending_async is not None and child_task_id in pending_async:
            atask, _ = pending_async[child_task_id]
            atask.cancel()
            del pending_async[child_task_id]

        # Mark the child task as done with cancellation error
        if child_task_id in state.tasks:
            cancelled_task = state.tasks[child_task_id].with_status(
                TaskDoneStatus(Err(TaskCancelledError()))  # type: ignore[arg-type]
            )
            state = state.with_task(child_task_id, cancelled_task)

        # Resume any join waiters with CancelledError
        state = self._resume_join_waiters(state, handle_id, join_waiters, spawned_tasks)

        # Return True to indicate cancellation was requested
        new_single = suspended.resume(True, state.store)
        return self._merge_task(state, task_id, new_single)

    def _handle_task_is_done(
        self,
        state: CESKState,
        task_id: TaskId,
        effect: TaskIsDoneEffect,
        suspended: Suspended,
        spawned_tasks: dict[Any, SpawnedTaskInfo],
    ) -> CESKState:
        """Handle TaskIsDoneEffect by checking task completion status."""
        handle_id = effect.task._handle

        if handle_id not in spawned_tasks:
            new_single = suspended.resume(True, state.store)
            return self._merge_task(state, task_id, new_single)

        spawned_info = spawned_tasks[handle_id]
        is_done = spawned_info.is_complete

        new_single = suspended.resume(is_done, state.store)
        return self._merge_task(state, task_id, new_single)

    def _handle_wait(
        self,
        state: CESKState,
        task_id: TaskId,
        effect: WaitEffect,
        suspended: Suspended,
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        join_waiters: dict[Any, list[tuple[TaskId, Suspended]]],
    ) -> CESKState:
        """Handle WaitEffect - waits for Future completion."""
        future = effect.future
        if not isinstance(future, Task):
            error_state = suspended.resume_error(
                TypeError(f"Wait requires a Task, got {type(future).__name__}")
            )
            return self._merge_task(state, task_id, error_state)

        handle_id = future._handle

        if handle_id not in spawned_tasks:
            error_state = suspended.resume_error(
                ValueError(f"Invalid task handle: {handle_id}")
            )
            return self._merge_task(state, task_id, error_state)

        spawned_info = spawned_tasks[handle_id]

        if spawned_info.is_complete:
            if spawned_info.is_cancelled:
                error_state = suspended.resume_error(TaskCancelledError())
                return self._merge_task(state, task_id, error_state)
            if spawned_info.error is not None:
                error_state = suspended.resume_error(spawned_info.error)
                return self._merge_task(state, task_id, error_state)
            new_single = suspended.resume(spawned_info.result, state.store)
            return self._merge_task(state, task_id, new_single)

        if handle_id not in join_waiters:
            join_waiters[handle_id] = []
        join_waiters[handle_id].append((task_id, suspended))

        return state

    def _handle_create_promise(
        self,
        state: CESKState,
        task_id: TaskId,
        suspended: Suspended,
        user_promises: dict[Any, Promise[Any]],
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        task_id_to_handle: dict[TaskId, Any],
    ) -> CESKState:
        handle_id = uuid4()
        task_handle = Task(backend="thread", _handle=handle_id)
        promise = Promise(_future=task_handle)

        spawned_info = SpawnedTaskInfo(
            task_id=TaskId.new(),
            env_snapshot={},
            store_snapshot={},
            is_complete=False,
        )
        spawned_tasks[handle_id] = spawned_info
        user_promises[handle_id] = promise

        new_single = suspended.resume(promise, state.store)
        return self._merge_task(state, task_id, new_single)

    def _handle_complete_promise(
        self,
        state: CESKState,
        task_id: TaskId,
        effect: CompletePromiseEffect,
        suspended: Suspended,
        user_promises: dict[Any, Promise[Any]],
        join_waiters: dict[Any, list[tuple[TaskId, Suspended]]],
        spawned_tasks: dict[Any, SpawnedTaskInfo],
    ) -> CESKState:
        promise = effect.promise
        handle_id = promise.future._handle

        if handle_id not in spawned_tasks:
            error_state = suspended.resume_error(
                ValueError(f"Invalid promise handle: {handle_id}")
            )
            return self._merge_task(state, task_id, error_state)

        spawned_info = spawned_tasks[handle_id]
        if spawned_info.is_complete:
            error_state = suspended.resume_error(
                RuntimeError("Promise already completed")
            )
            return self._merge_task(state, task_id, error_state)

        spawned_info.is_complete = True
        spawned_info.result = effect.value

        state = self._resume_join_waiters(state, handle_id, join_waiters, spawned_tasks)

        new_single = suspended.resume(None, state.store)
        return self._merge_task(state, task_id, new_single)

    def _handle_fail_promise(
        self,
        state: CESKState,
        task_id: TaskId,
        effect: FailPromiseEffect,
        suspended: Suspended,
        user_promises: dict[Any, Promise[Any]],
        join_waiters: dict[Any, list[tuple[TaskId, Suspended]]],
        spawned_tasks: dict[Any, SpawnedTaskInfo],
    ) -> CESKState:
        promise = effect.promise
        handle_id = promise.future._handle

        if handle_id not in spawned_tasks:
            error_state = suspended.resume_error(
                ValueError(f"Invalid promise handle: {handle_id}")
            )
            return self._merge_task(state, task_id, error_state)

        spawned_info = spawned_tasks[handle_id]
        if spawned_info.is_complete:
            error_state = suspended.resume_error(
                RuntimeError("Promise already completed")
            )
            return self._merge_task(state, task_id, error_state)

        spawned_info.is_complete = True
        spawned_info.error = effect.error

        state = self._resume_join_waiters(state, handle_id, join_waiters, spawned_tasks)

        new_single = suspended.resume(None, state.store)
        return self._merge_task(state, task_id, new_single)

    def _resume_join_waiters(
        self,
        state: CESKState,
        handle_id: Any,
        join_waiters: dict[Any, list[tuple[TaskId, Suspended]]],
        spawned_tasks: dict[Any, SpawnedTaskInfo],
    ) -> CESKState:
        """Resume all tasks waiting to join a completed spawned task."""
        if handle_id not in join_waiters:
            return state

        waiters = join_waiters.pop(handle_id)
        spawned_info = spawned_tasks[handle_id]

        for waiter_task_id, suspended in waiters:
            if spawned_info.is_cancelled:
                error_state = suspended.resume_error(TaskCancelledError())
                error_state = self._fix_store_rollback(error_state, state.store)
                state = self._merge_task(state, waiter_task_id, error_state)
            elif spawned_info.error is not None:
                # Preserve traceback on error
                error = spawned_info.error
                error_state = suspended.resume_error(error)
                error_state = self._fix_store_rollback(error_state, state.store)
                state = self._merge_task(state, waiter_task_id, error_state)
            else:
                new_single = suspended.resume(spawned_info.result, state.store)
                state = self._merge_task(state, waiter_task_id, new_single)

        # Note: We don't clean up spawned_tasks here because:
        # 1. Multiple joins on the same task should return the same result
        # 2. The tracking dicts are scoped to a single run() call anyway
        # 3. Memory is freed when run() completes

        return state

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

    def _make_single_task_state(
        self,
        state: CESKState,
        task_id: TaskId,
        isolated_store: Store | None = None
    ) -> CESKState:
        """Create a single-task CESKState for stepping.
        
        Args:
            state: The current CESK state
            task_id: The task to create state for
            isolated_store: If provided, use this isolated store instead of shared store
                           (used for spawned tasks with snapshot semantics)
        """
        task = state.tasks[task_id]
        return CESKState(
            tasks={task_id: task},
            store=isolated_store if isolated_store is not None else state.store,
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

    def _apply_dispatch_result_isolated(
        self,
        state: CESKState,
        task_id: TaskId,
        suspended: Suspended,
        dispatch_result: FrameResult,
        spawned_info: SpawnedTaskInfo,
    ) -> CESKState:
        """Apply handler dispatch result for spawned task (isolated store)."""
        if isinstance(dispatch_result, ContinueError):
            new_single = suspended.resume_error(dispatch_result.error)
            # Update spawned task's isolated store
            spawned_info.store_snapshot = new_single.store
            # Only merge task state, keep parent's store
            new_tasks = dict(state.tasks)
            new_tasks[task_id] = new_single.tasks[new_single.main_task]
            return CESKState(
                tasks=new_tasks,
                store=state.store,
                main_task=state.main_task,
                futures=state.futures,
                spawn_results=state.spawn_results,
            )
        if isinstance(dispatch_result, ContinueProgram):
            # Update spawned task's isolated store
            spawned_info.store_snapshot = dispatch_result.store
            temp_state = CESKState(
                C=ProgramControl(dispatch_result.program),
                E=dispatch_result.env,
                S=dispatch_result.store,
                K=dispatch_result.k,
            )
            # Only merge task state, keep parent's store
            new_tasks = dict(state.tasks)
            new_tasks[task_id] = temp_state.tasks[temp_state.main_task]
            return CESKState(
                tasks=new_tasks,
                store=state.store,
                main_task=state.main_task,
                futures=state.futures,
                spawn_results=state.spawn_results,
            )
        if isinstance(dispatch_result, ContinueValue):
            new_single = suspended.resume(dispatch_result.value, dispatch_result.store)
            # Update spawned task's isolated store
            spawned_info.store_snapshot = dispatch_result.store
            # Only merge task state, keep parent's store
            new_tasks = dict(state.tasks)
            new_tasks[task_id] = new_single.tasks[new_single.main_task]
            return CESKState(
                tasks=new_tasks,
                store=state.store,
                main_task=state.main_task,
                futures=state.futures,
                spawn_results=state.spawn_results,
            )
        raise RuntimeError(f"Unexpected dispatch result type: {type(dispatch_result)}")

    def _check_gather_complete(
        self,
        state: CESKState,
        completed_id: TaskId,
        gather_waiters: dict[TaskId, tuple[list[TaskId], Suspended]],
        gather_task_meta: dict[TaskId, tuple[tuple[Future[Any], ...], dict[TaskId, int]]],
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        task_id_to_handle: dict[TaskId, Any],
    ) -> CESKState:
        for parent_id, (child_ids, suspended) in list(gather_waiters.items()):
            if completed_id not in child_ids:
                continue

            if parent_id not in gather_task_meta:
                continue

            futures, task_to_index = gather_task_meta[parent_id]

            handle_id = task_id_to_handle.get(completed_id)
            if handle_id and handle_id in spawned_tasks:
                spawned_info = spawned_tasks[handle_id]
                if spawned_info.error is not None:
                    del gather_waiters[parent_id]
                    del gather_task_meta[parent_id]
                    error_state = suspended.resume_error(spawned_info.error)
                    error_state = self._fix_store_rollback(error_state, state.store)
                    return self._merge_task(state, parent_id, error_state)

            all_done = True
            for cid in child_ids:
                hid = task_id_to_handle.get(cid)
                if hid and hid in spawned_tasks:
                    if not spawned_tasks[hid].is_complete:
                        all_done = False
                        break
                else:
                    all_done = False
                    break

            if all_done:
                del gather_waiters[parent_id]
                del gather_task_meta[parent_id]
                results: list[Any] = [None] * len(futures)
                for future in futures:
                    hid = future._handle
                    if hid in spawned_tasks:
                        cid = spawned_tasks[hid].task_id
                        idx = task_to_index[cid]
                        results[idx] = spawned_tasks[hid].result
                new_single = suspended.resume(results, state.store)
                return self._merge_task(state, parent_id, new_single)

        return state

    def _handle_gather_futures(
        self,
        state: CESKState,
        task_id: TaskId,
        futures: tuple[Future[Any], ...],
        suspended: Suspended,
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        gather_waiters: dict[TaskId, tuple[list[TaskId], Suspended]],
        gather_task_meta: dict[TaskId, tuple[tuple[Future[Any], ...], dict[TaskId, int]]],
    ) -> CESKState:
        task_to_index: dict[TaskId, int] = {}
        pending_task_ids: list[TaskId] = []

        for i, future in enumerate(futures):
            handle_id = future._handle
            if handle_id not in spawned_tasks:
                error_state = suspended.resume_error(
                    ValueError(f"Invalid future handle: {handle_id}")
                )
                return self._merge_task(state, task_id, error_state)

            spawned_info = spawned_tasks[handle_id]
            child_task_id = spawned_info.task_id

            if spawned_info.is_complete:
                if spawned_info.error is not None:
                    error_state = suspended.resume_error(spawned_info.error)
                    return self._merge_task(state, task_id, error_state)
            else:
                pending_task_ids.append(child_task_id)

            task_to_index[child_task_id] = i

        if not pending_task_ids:
            results = []
            for future in futures:
                handle_id = future._handle
                spawned_info = spawned_tasks[handle_id]
                results.append(spawned_info.result)
            new_single = suspended.resume(results, state.store)
            return self._merge_task(state, task_id, new_single)

        gather_waiters[task_id] = (pending_task_ids, suspended)
        gather_task_meta[task_id] = (futures, task_to_index)
        return state

    def _handle_race_futures(
        self,
        state: CESKState,
        task_id: TaskId,
        futures: tuple[Future[Any], ...],
        suspended: Suspended,
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        race_waiters: dict[TaskId, tuple[list[TaskId], Suspended]],
        race_task_meta: dict[TaskId, tuple[tuple[Future[Any], ...], dict[TaskId, Future[Any]]]],
    ) -> CESKState:
        for future in futures:
            handle_id = future._handle
            if handle_id in spawned_tasks:
                spawned_info = spawned_tasks[handle_id]
                if spawned_info.is_complete:
                    if spawned_info.error is not None:
                        error_state = suspended.resume_error(spawned_info.error)
                        return self._merge_task(state, task_id, error_state)
                    rest = tuple(f for f in futures if f is not future)
                    race_result = RaceResult(first=future, value=spawned_info.result, rest=rest)
                    new_single = suspended.resume(race_result, state.store)
                    return self._merge_task(state, task_id, new_single)

        future_to_task_id = {spawned_tasks[f._handle].task_id: f for f in futures if f._handle in spawned_tasks}
        child_ids = list(future_to_task_id.keys())
        
        race_waiters[task_id] = (child_ids, suspended)
        race_task_meta[task_id] = (futures, future_to_task_id)
        return state

    def _check_race_complete(
        self,
        state: CESKState,
        completed_id: TaskId,
        race_waiters: dict[TaskId, tuple[list[TaskId], Suspended]],
        task_results: dict[TaskId, Any],
        task_errors: dict[TaskId, BaseException],
        race_task_meta: dict[TaskId, tuple[tuple[Future[Any], ...], dict[TaskId, Future[Any]]]],
    ) -> CESKState:
        for parent_id, (child_ids, suspended) in list(race_waiters.items()):
            if completed_id not in child_ids:
                continue

            if completed_id in task_errors:
                del race_waiters[parent_id]
                if parent_id in race_task_meta:
                    del race_task_meta[parent_id]
                error_state = suspended.resume_error(task_errors[completed_id])
                error_state = self._fix_store_rollback(error_state, state.store)
                return self._merge_task(state, parent_id, error_state)

            if completed_id in task_results:
                del race_waiters[parent_id]
                race_meta = race_task_meta.pop(parent_id, None)
                if race_meta is None:
                    raise RuntimeError("Race metadata missing - internal error")
                
                futures, task_to_future = race_meta
                winner = task_to_future.get(completed_id)
                if winner is None:
                    raise RuntimeError("Race winner not found - internal error")
                
                rest = tuple(f for f in futures if f is not winner)
                race_result = RaceResult(first=winner, value=task_results[completed_id], rest=rest)
                new_single = suspended.resume(race_result, state.store)
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
