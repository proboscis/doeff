"""Synchronous runtime with cooperative scheduling and external suspension support.

This runtime implements:
- Cooperative multi-task scheduling via Scheduler
- External suspension for Await effects via ThreadedAsyncioExecutor
- Spawn/Wait/Task handling with snapshot semantics

Unlike AsyncRuntime which uses asyncio, SyncRuntime runs in a single thread
and uses a background thread pool for async operations (Await effects).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import uuid4

from doeff._vendor import Err
from doeff.cesk.frames import (
    ContinueError,
    ContinueProgram,
    ContinueValue,
    FrameResult,
    SuspendOn,
)
from doeff.cesk.handlers import Handler, default_handlers
from doeff.cesk.result import Done, Failed, Suspended
from doeff.cesk.runtime.base import BaseRuntime, ExecutionError
from doeff.cesk.runtime.context import HandlerContext
from doeff.cesk.runtime.executor import AsyncExecutor, ThreadedAsyncioExecutor
from doeff.cesk.runtime.scheduler import (
    InitialState,
    PendingComplete,
    PendingFail,
    ResumeWithError,
    ResumeWithState,
    ResumeWithValue,
    Scheduler,
)
from doeff.cesk.runtime_result import RuntimeResult
from doeff.cesk.state import CESKState, ProgramControl, TaskState
from doeff.cesk.state import Done as TaskDoneStatus
from doeff.cesk.step import step
from doeff.cesk.types import Store, TaskId
from doeff.effects.future import FutureAwaitEffect
from doeff.effects.gather import GatherEffect
from doeff.effects.promise import (
    CompletePromiseEffect,
    CreatePromiseEffect,
    FailPromiseEffect,
)
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
from doeff.effects.time import DelayEffect, WaitUntilEffect
from doeff.effects.wait import WaitEffect

if TYPE_CHECKING:
    from doeff.program import Program

T = TypeVar("T")


def _placeholder_handler(effect: Any, ctx: HandlerContext) -> ContinueValue:
    """Placeholder handler for effects intercepted by runtime."""
    return ContinueValue(
        value=None,
        env=ctx.task_state.env,
        store=ctx.store,
        k=ctx.task_state.kontinuation,
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


class SyncRuntime(BaseRuntime):
    """Synchronous runtime with cooperative scheduling and external suspension.

    This runtime executes programs synchronously but supports:
    - Multi-task execution via cooperative scheduling
    - Spawn/Wait for background task creation
    - Await for external async operations (run in background thread)
    - Gather/Race for parallel Future completion

    The runtime uses a Scheduler for task management and a ThreadedAsyncioExecutor
    for running async awaitables in a background thread.
    """

    def __init__(
        self,
        handlers: dict[type, Handler] | None = None,
        executor: AsyncExecutor | None = None,
    ):
        """Initialize SyncRuntime.

        Args:
            handlers: Optional custom handlers. These override defaults.
            executor: Optional AsyncExecutor for running awaitables.
                     Defaults to ThreadedAsyncioExecutor.
        """
        base_handlers = default_handlers()
        base_handlers[DelayEffect] = _placeholder_handler
        base_handlers[WaitUntilEffect] = _placeholder_handler
        base_handlers[FutureAwaitEffect] = _placeholder_handler
        base_handlers[GatherEffect] = _placeholder_handler
        base_handlers[RaceEffect] = _placeholder_handler
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
        self._executor = executor
        self._owns_executor = False

    def _get_executor(self) -> AsyncExecutor:
        """Get or create the async executor."""
        if self._executor is None:
            self._executor = ThreadedAsyncioExecutor()
            self._owns_executor = True
        return self._executor

    def _shutdown_executor(self) -> None:
        """Shutdown executor if we own it."""
        if self._owns_executor and self._executor is not None:
            self._executor.shutdown()
            self._executor = None
            self._owns_executor = False

    def run(
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

        Raises:
            KeyboardInterrupt, SystemExit: These are re-raised, not wrapped,
                as they represent external control signals, not program errors.
        """
        state = self._create_initial_state(program, env, store)

        try:
            value, final_state, final_store = self._run_scheduler(state)
            return self._build_success_result(value, final_state, final_store)
        except ExecutionError as err:
            if isinstance(err.exception, (KeyboardInterrupt, SystemExit)):
                raise err.exception from None
            return self._build_error_result(
                err.exception,
                err.final_state,
                captured_traceback=err.captured_traceback,
            )
        finally:
            self._shutdown_executor()

    def run_and_unwrap(
        self,
        program: Program[T],
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> T:
        """Execute a program and return just the value (raises on error).

        This is a convenience method for when you don't need the full
        RuntimeResult context. Equivalent to `run(...).value`.

        Args:
            program: The program to execute
            env: Optional initial environment
            store: Optional initial store

        Returns:
            The program's return value

        Raises:
            Any exception raised during program execution
        """
        result = self.run(program, env, store)
        return result.value

    def _run_scheduler(  # noqa: PLR0912, PLR0915
        self, state: CESKState
    ) -> tuple[Any, CESKState, Store]:
        """Run the cooperative scheduler loop until main task completes.

        Returns:
            Tuple of (final_value, final_state, final_store)

        Raises:
            ExecutionError: On failure, containing the exception and final state
        """
        scheduler = Scheduler()
        main_task_id = state.main_task

        spawned_tasks: dict[Any, SpawnedTaskInfo] = {}
        task_id_to_handle: dict[TaskId, Any] = {}
        join_waiters: dict[Any, list[tuple[TaskId, Suspended]]] = {}
        user_promises: dict[Any, Promise[Any]] = {}
        gather_waiters: dict[TaskId, tuple[list[TaskId], Suspended]] = {}
        gather_task_meta: dict[
            TaskId, tuple[tuple[Future[Any], ...], dict[TaskId, int]]
        ] = {}
        race_waiters: dict[TaskId, tuple[list[TaskId], Suspended]] = {}
        race_task_meta: dict[
            TaskId, tuple[tuple[Future[Any], ...], dict[TaskId, Future[Any]]]
        ] = {}
        task_results: dict[TaskId, Any] = {}
        task_errors: dict[TaskId, BaseException] = {}

        scheduler.enqueue_ready(main_task_id, InitialState(state))

        while True:
            timeout = None if scheduler.has_pending() else 0
            next_item = scheduler.get_next(timeout=timeout)

            if next_item is None:
                if state.is_main_task_done():
                    main_result = state.get_main_result()
                    if main_result is not None:
                        if main_result.is_ok():
                            return (main_result.ok(), state, state.store)
                        raise ExecutionError(
                            exception=main_result.err(),  # type: ignore[arg-type]
                            final_state=state,
                        )
                if scheduler.has_pending():
                    continue
                raise ExecutionError(
                    exception=RuntimeError("Deadlock: no ready tasks and nothing pending"),
                    final_state=state,
                )

            task_id, resume_info = next_item

            if isinstance(resume_info, InitialState):
                new_tasks = dict(state.tasks)
                for tid, task_state in resume_info.state.tasks.items():
                    new_tasks[tid] = task_state
                state = CESKState(
                    tasks=new_tasks,
                    store=state.store,
                    main_task=state.main_task,
                    futures=state.futures,
                    spawn_results=state.spawn_results,
                )
            elif isinstance(resume_info, ResumeWithState):
                new_tasks = dict(state.tasks)
                if task_id in resume_info.state.tasks:
                    new_tasks[task_id] = resume_info.state.tasks[task_id]
                else:
                    new_tasks[task_id] = resume_info.state.tasks[resume_info.state.main_task]
                state = CESKState(
                    tasks=new_tasks,
                    store=resume_info.state.store,
                    main_task=state.main_task,
                    futures=state.futures,
                    spawn_results=state.spawn_results,
                )
            elif isinstance(resume_info, ResumeWithValue):
                state = CESKState(
                    tasks=state.tasks,
                    store=resume_info.store,
                    main_task=state.main_task,
                    futures=state.futures,
                    spawn_results=state.spawn_results,
                )
            elif isinstance(resume_info, ResumeWithError):
                pass
            elif isinstance(resume_info, PendingComplete):
                pending = scheduler.pop_pending(resume_info.handle)
                if pending is None:
                    continue
                _pending_task_id, suspended = pending
                store_for_resume = self._get_store_for_task(
                    task_id, state, spawned_tasks, task_id_to_handle
                )
                new_state = suspended.resume(resume_info.value, store_for_resume)
                new_tasks = dict(state.tasks)
                if task_id in new_state.tasks:
                    new_tasks[task_id] = new_state.tasks[task_id]
                else:
                    new_tasks[task_id] = new_state.tasks[new_state.main_task]
                if task_id in task_id_to_handle:
                    handle_id = task_id_to_handle[task_id]
                    spawned_tasks[handle_id].store_snapshot = new_state.store
                    state = CESKState(
                        tasks=new_tasks,
                        store=state.store,
                        main_task=state.main_task,
                        futures=state.futures,
                        spawn_results=state.spawn_results,
                    )
                else:
                    state = CESKState(
                        tasks=new_tasks,
                        store=new_state.store,
                        main_task=state.main_task,
                        futures=state.futures,
                        spawn_results=state.spawn_results,
                    )
            elif isinstance(resume_info, PendingFail):
                pending = scheduler.pop_pending(resume_info.handle)
                if pending is None:
                    continue
                _pending_task_id, suspended = pending
                new_state = suspended.resume_error(resume_info.error)
                new_tasks = dict(state.tasks)
                if task_id in new_state.tasks:
                    new_tasks[task_id] = new_state.tasks[task_id]
                else:
                    new_tasks[task_id] = new_state.tasks[new_state.main_task]
                state = CESKState(
                    tasks=new_tasks,
                    store=state.store,
                    main_task=state.main_task,
                    futures=state.futures,
                    spawn_results=state.spawn_results,
                )

            if task_id not in state.tasks:
                continue

            isolated_store = None
            if task_id in task_id_to_handle:
                handle_id = task_id_to_handle[task_id]
                spawned_info = spawned_tasks[handle_id]
                isolated_store = spawned_info.store_snapshot

            single_state = self._make_single_task_state(state, task_id, isolated_store)
            result = step(single_state, self._handlers)

            if isinstance(result, Done):
                is_spawned_task = task_id in task_id_to_handle

                if not is_spawned_task:
                    state = self._update_store(state, result.store)

                if task_id == main_task_id:
                    return (result.value, state, result.store)

                done_task = state.tasks[task_id].with_status(
                    TaskDoneStatus.ok(result.value)
                )
                state = state.with_task(task_id, done_task)
                task_results[task_id] = result.value

                if is_spawned_task:
                    handle_id = task_id_to_handle[task_id]
                    spawned_info = spawned_tasks[handle_id]
                    spawned_info.is_complete = True
                    spawned_info.result = result.value
                    spawned_info.store_snapshot = result.store
                    state = self._resume_join_waiters(
                        state, handle_id, join_waiters, spawned_tasks, scheduler
                    )

                state = self._check_gather_complete(
                    state,
                    task_id,
                    gather_waiters,
                    gather_task_meta,
                    spawned_tasks,
                    task_id_to_handle,
                    scheduler,
                )
                state = self._check_race_complete(
                    state,
                    task_id,
                    race_waiters,
                    task_results,
                    task_errors,
                    race_task_meta,
                    scheduler,
                )
                continue

            if isinstance(result, Failed):
                is_spawned_task = task_id in task_id_to_handle

                if not is_spawned_task:
                    state = self._update_store(state, result.store)

                if task_id == main_task_id:
                    exc = result.exception
                    if result.captured_traceback is not None:
                        exc.__cesk_traceback__ = result.captured_traceback  # type: ignore[attr-defined]
                    raise ExecutionError(
                        exception=exc,
                        final_state=state,
                        captured_traceback=result.captured_traceback,
                    )

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
                        state, handle_id, join_waiters, spawned_tasks, scheduler
                    )

                state = self._check_gather_complete(
                    state,
                    task_id,
                    gather_waiters,
                    gather_task_meta,
                    spawned_tasks,
                    task_id_to_handle,
                    scheduler,
                )
                state = self._check_race_complete(
                    state,
                    task_id,
                    race_waiters,
                    task_results,
                    task_errors,
                    race_task_meta,
                    scheduler,
                )
                continue

            if isinstance(result, CESKState):
                is_spawned_task = task_id in task_id_to_handle

                if is_spawned_task:
                    handle_id = task_id_to_handle[task_id]
                    spawned_info = spawned_tasks[handle_id]
                    spawned_info.store_snapshot = result.store
                    new_tasks = dict(state.tasks)
                    new_tasks[task_id] = result.tasks[result.main_task]
                    state = CESKState(
                        tasks=new_tasks,
                        store=state.store,
                        main_task=state.main_task,
                        futures=state.futures,
                        spawn_results=state.spawn_results,
                    )
                else:
                    state = self._merge_task(state, task_id, result)
                scheduler.enqueue_ready(task_id, ResumeWithState(state))
                continue

            if isinstance(result, Suspended):
                effect = result.effect
                effect_type = type(effect)

                if effect_type in self._user_handlers:
                    task_state = state.tasks[task_id]
                    store_for_dispatch = self._get_store_for_task(
                        task_id, state, spawned_tasks, task_id_to_handle
                    )
                    dispatch_result = self._dispatch_effect(
                        effect, task_state, store_for_dispatch
                    )

                    if task_id in task_id_to_handle:
                        handle_id = task_id_to_handle[task_id]
                        spawned_info = spawned_tasks[handle_id]
                        state = self._apply_dispatch_result_isolated(
                            state, task_id, result, dispatch_result, spawned_info
                        )
                    else:
                        state = self._apply_dispatch_result(
                            state, task_id, result, dispatch_result
                        )
                    scheduler.enqueue_ready(task_id, ResumeWithState(state))
                    continue

                if isinstance(effect, SpawnEffect):
                    state = self._handle_spawn(
                        state,
                        task_id,
                        effect,
                        result,
                        spawned_tasks,
                        task_id_to_handle,
                        scheduler,
                    )
                    continue

                if isinstance(effect, WaitEffect):
                    state = self._handle_wait(
                        state,
                        task_id,
                        effect,
                        result,
                        spawned_tasks,
                        join_waiters,
                        scheduler,
                    )
                    continue

                if isinstance(effect, TaskCancelEffect):
                    state = self._handle_task_cancel(
                        state,
                        task_id,
                        effect,
                        result,
                        spawned_tasks,
                        join_waiters,
                        scheduler,
                    )
                    continue

                if isinstance(effect, TaskIsDoneEffect):
                    state = self._handle_task_is_done(
                        state, task_id, effect, result, spawned_tasks, scheduler
                    )
                    continue

                if isinstance(effect, CreatePromiseEffect):
                    state = self._handle_create_promise(
                        state,
                        task_id,
                        result,
                        user_promises,
                        spawned_tasks,
                        task_id_to_handle,
                        scheduler,
                    )
                    continue

                if isinstance(effect, CompletePromiseEffect):
                    state = self._handle_complete_promise(
                        state,
                        task_id,
                        effect,
                        result,
                        user_promises,
                        join_waiters,
                        spawned_tasks,
                        scheduler,
                    )
                    continue

                if isinstance(effect, FailPromiseEffect):
                    state = self._handle_fail_promise(
                        state,
                        task_id,
                        effect,
                        result,
                        user_promises,
                        join_waiters,
                        spawned_tasks,
                        scheduler,
                    )
                    continue

                if isinstance(effect, GatherEffect):
                    futures = effect.futures
                    if not futures:
                        new_single = result.resume([], state.store)
                        state = self._merge_task(state, task_id, new_single)
                        scheduler.enqueue_ready(task_id, ResumeWithState(state))
                        continue

                    state = self._handle_gather_futures(
                        state,
                        task_id,
                        futures,
                        result,
                        spawned_tasks,
                        gather_waiters,
                        gather_task_meta,
                    )
                    continue

                if isinstance(effect, RaceEffect):
                    state = self._handle_race_futures(
                        state,
                        task_id,
                        effect.futures,
                        result,
                        spawned_tasks,
                        race_waiters,
                        race_task_meta,
                        scheduler,
                    )
                    continue

                if isinstance(effect, FutureAwaitEffect):
                    self._handle_await(
                        state,
                        task_id,
                        effect,
                        result,
                        spawned_tasks,
                        task_id_to_handle,
                        scheduler,
                    )
                    continue

                if isinstance(effect, DelayEffect):
                    self._handle_delay(
                        state,
                        task_id,
                        effect,
                        result,
                        spawned_tasks,
                        task_id_to_handle,
                        scheduler,
                    )
                    continue

                if isinstance(effect, WaitUntilEffect):
                    self._handle_wait_until(
                        state,
                        task_id,
                        effect,
                        result,
                        spawned_tasks,
                        task_id_to_handle,
                        scheduler,
                    )
                    continue

                task_state = state.tasks[task_id]
                store_for_dispatch = self._get_store_for_task(
                    task_id, state, spawned_tasks, task_id_to_handle
                )
                dispatch_result = self._dispatch_effect(
                    effect, task_state, store_for_dispatch
                )

                if task_id in task_id_to_handle:
                    handle_id = task_id_to_handle[task_id]
                    spawned_info = spawned_tasks[handle_id]
                    state = self._apply_dispatch_result_isolated(
                        state, task_id, result, dispatch_result, spawned_info
                    )
                else:
                    state = self._apply_dispatch_result(
                        state, task_id, result, dispatch_result
                    )
                scheduler.enqueue_ready(task_id, ResumeWithState(state))
                continue

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

    def _make_single_task_state(
        self,
        state: CESKState,
        task_id: TaskId,
        isolated_store: Store | None = None,
    ) -> CESKState:
        """Create a single-task CESKState for stepping."""
        task = state.tasks[task_id]
        return CESKState(
            tasks={task_id: task},
            store=isolated_store if isolated_store is not None else state.store,
            main_task=task_id,
            futures=state.futures,
            spawn_results=state.spawn_results,
        )

    def _merge_task(
        self, state: CESKState, task_id: TaskId, stepped: CESKState
    ) -> CESKState:
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

    def _update_store(self, state: CESKState, store: Store) -> CESKState:
        """Update store in state."""
        return CESKState(
            tasks=state.tasks,
            store=store,
            main_task=state.main_task,
            futures=state.futures,
            spawn_results=state.spawn_results,
        )

    def _fix_store_rollback(
        self, error_state: CESKState, current_store: Store
    ) -> CESKState:
        """Fix store in error state to use current store (no rollback)."""
        return CESKState(
            tasks={error_state.main_task: error_state.tasks[error_state.main_task]},
            store=current_store,
            main_task=error_state.main_task,
            futures=error_state.futures,
            spawn_results=error_state.spawn_results,
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
            temp_state = CESKState(
                C=ProgramControl(dispatch_result.program),
                E=dispatch_result.env,
                S=dispatch_result.store,
                K=dispatch_result.k,
            )
            return self._merge_task(state, task_id, temp_state)
        if isinstance(dispatch_result, ContinueValue):
            new_single = suspended.resume(dispatch_result.value, dispatch_result.store)
            return self._merge_task(state, task_id, new_single)
        if isinstance(dispatch_result, SuspendOn):
            return state
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
            spawned_info.store_snapshot = new_single.store
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
            spawned_info.store_snapshot = dispatch_result.store
            temp_state = CESKState(
                C=ProgramControl(dispatch_result.program),
                E=dispatch_result.env,
                S=dispatch_result.store,
                K=dispatch_result.k,
            )
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
            spawned_info.store_snapshot = dispatch_result.store
            new_tasks = dict(state.tasks)
            new_tasks[task_id] = new_single.tasks[new_single.main_task]
            return CESKState(
                tasks=new_tasks,
                store=state.store,
                main_task=state.main_task,
                futures=state.futures,
                spawn_results=state.spawn_results,
            )
        if isinstance(dispatch_result, SuspendOn):
            return state
        raise RuntimeError(f"Unexpected dispatch result type: {type(dispatch_result)}")

    # ========== Spawn/Task Effects ==========

    def _handle_spawn(
        self,
        state: CESKState,
        task_id: TaskId,
        effect: SpawnEffect,
        suspended: Suspended,
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        task_id_to_handle: dict[TaskId, Any],
        scheduler: Scheduler,
    ) -> CESKState:
        """Handle SpawnEffect by creating a new background task with snapshot semantics."""
        handle_id = uuid4()

        current_task = state.tasks[task_id]
        env_snapshot = dict(current_task.env)

        if task_id in task_id_to_handle:
            parent_handle_id = task_id_to_handle[task_id]
            parent_info = spawned_tasks[parent_handle_id]
            store_snapshot = dict(parent_info.store_snapshot)
        else:
            store_snapshot = dict(state.store)

        child_id = TaskId.new()
        child_task = TaskState.initial(effect.program, env_snapshot)  # type: ignore[arg-type]
        state = state.add_task(child_id, child_task)

        backend = effect.preferred_backend or "thread"

        task_handle = Task(
            backend=backend,
            _handle=handle_id,
            _env_snapshot=env_snapshot,
            _state_snapshot=store_snapshot,
        )

        spawned_info = SpawnedTaskInfo(
            task_id=child_id,
            env_snapshot=env_snapshot,
            store_snapshot=store_snapshot,
        )
        spawned_tasks[handle_id] = spawned_info
        task_id_to_handle[child_id] = handle_id

        new_single = suspended.resume(task_handle, state.store)
        state = self._merge_task(state, task_id, new_single)
        scheduler.enqueue_ready(task_id, ResumeWithState(state))

        child_state = CESKState(
            tasks={child_id: child_task},
            store=store_snapshot,
            main_task=child_id,
            futures=state.futures,
            spawn_results=state.spawn_results,
        )
        scheduler.enqueue_ready(child_id, InitialState(child_state))

        return state

    def _handle_wait(
        self,
        state: CESKState,
        task_id: TaskId,
        effect: WaitEffect,
        suspended: Suspended,
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        join_waiters: dict[Any, list[tuple[TaskId, Suspended]]],
        scheduler: Scheduler,
    ) -> CESKState:
        """Handle WaitEffect - waits for Future completion."""
        future = effect.future
        if not isinstance(future, Task):
            error_state = suspended.resume_error(
                TypeError(f"Wait requires a Task, got {type(future).__name__}")
            )
            state = self._merge_task(state, task_id, error_state)
            scheduler.enqueue_ready(task_id, ResumeWithState(state))
            return state

        handle_id = future._handle

        if handle_id not in spawned_tasks:
            error_state = suspended.resume_error(
                ValueError(f"Invalid task handle: {handle_id}")
            )
            state = self._merge_task(state, task_id, error_state)
            scheduler.enqueue_ready(task_id, ResumeWithState(state))
            return state

        spawned_info = spawned_tasks[handle_id]

        if spawned_info.is_complete:
            if spawned_info.is_cancelled:
                error_state = suspended.resume_error(TaskCancelledError())
                state = self._merge_task(state, task_id, error_state)
            elif spawned_info.error is not None:
                error_state = suspended.resume_error(spawned_info.error)
                state = self._merge_task(state, task_id, error_state)
            else:
                new_single = suspended.resume(spawned_info.result, state.store)
                state = self._merge_task(state, task_id, new_single)
            scheduler.enqueue_ready(task_id, ResumeWithState(state))
            return state

        if handle_id not in join_waiters:
            join_waiters[handle_id] = []
        join_waiters[handle_id].append((task_id, suspended))

        return state

    def _handle_task_cancel(
        self,
        state: CESKState,
        task_id: TaskId,
        effect: TaskCancelEffect,
        suspended: Suspended,
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        join_waiters: dict[Any, list[tuple[TaskId, Suspended]]],
        scheduler: Scheduler,
    ) -> CESKState:
        """Handle TaskCancelEffect by requesting task cancellation."""
        handle_id = effect.task._handle

        if handle_id not in spawned_tasks:
            new_single = suspended.resume(False, state.store)
            state = self._merge_task(state, task_id, new_single)
            scheduler.enqueue_ready(task_id, ResumeWithState(state))
            return state

        spawned_info = spawned_tasks[handle_id]

        if spawned_info.is_complete:
            new_single = suspended.resume(False, state.store)
            state = self._merge_task(state, task_id, new_single)
            scheduler.enqueue_ready(task_id, ResumeWithState(state))
            return state

        spawned_info.is_cancelled = True
        spawned_info.is_complete = True
        spawned_info.error = TaskCancelledError()

        child_task_id = spawned_info.task_id

        if child_task_id in state.tasks:
            cancelled_task = state.tasks[child_task_id].with_status(
                TaskDoneStatus(Err(TaskCancelledError()))  # type: ignore[arg-type]
            )
            state = state.with_task(child_task_id, cancelled_task)

        state = self._resume_join_waiters(
            state, handle_id, join_waiters, spawned_tasks, scheduler
        )

        new_single = suspended.resume(True, state.store)
        state = self._merge_task(state, task_id, new_single)
        scheduler.enqueue_ready(task_id, ResumeWithState(state))
        return state

    def _handle_task_is_done(
        self,
        state: CESKState,
        task_id: TaskId,
        effect: TaskIsDoneEffect,
        suspended: Suspended,
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        scheduler: Scheduler,
    ) -> CESKState:
        """Handle TaskIsDoneEffect by checking task completion status."""
        handle_id = effect.task._handle

        if handle_id not in spawned_tasks:
            new_single = suspended.resume(True, state.store)
            state = self._merge_task(state, task_id, new_single)
            scheduler.enqueue_ready(task_id, ResumeWithState(state))
            return state

        spawned_info = spawned_tasks[handle_id]
        is_done = spawned_info.is_complete

        new_single = suspended.resume(is_done, state.store)
        state = self._merge_task(state, task_id, new_single)
        scheduler.enqueue_ready(task_id, ResumeWithState(state))
        return state

    def _resume_join_waiters(
        self,
        state: CESKState,
        handle_id: Any,
        join_waiters: dict[Any, list[tuple[TaskId, Suspended]]],
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        scheduler: Scheduler,
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
                error_state = suspended.resume_error(spawned_info.error)
                error_state = self._fix_store_rollback(error_state, state.store)
                state = self._merge_task(state, waiter_task_id, error_state)
            else:
                new_single = suspended.resume(spawned_info.result, state.store)
                state = self._merge_task(state, waiter_task_id, new_single)

            scheduler.enqueue_ready(waiter_task_id, ResumeWithState(state))

        return state

    # ========== Promise Effects ==========

    def _handle_create_promise(
        self,
        state: CESKState,
        task_id: TaskId,
        suspended: Suspended,
        user_promises: dict[Any, Promise[Any]],
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        task_id_to_handle: dict[TaskId, Any],
        scheduler: Scheduler,
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
        state = self._merge_task(state, task_id, new_single)
        scheduler.enqueue_ready(task_id, ResumeWithState(state))
        return state

    def _handle_complete_promise(
        self,
        state: CESKState,
        task_id: TaskId,
        effect: CompletePromiseEffect,
        suspended: Suspended,
        user_promises: dict[Any, Promise[Any]],
        join_waiters: dict[Any, list[tuple[TaskId, Suspended]]],
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        scheduler: Scheduler,
    ) -> CESKState:
        promise = effect.promise
        handle_id = promise.future._handle

        if handle_id not in spawned_tasks:
            error_state = suspended.resume_error(
                ValueError(f"Invalid promise handle: {handle_id}")
            )
            state = self._merge_task(state, task_id, error_state)
            scheduler.enqueue_ready(task_id, ResumeWithState(state))
            return state

        spawned_info = spawned_tasks[handle_id]
        if spawned_info.is_complete:
            error_state = suspended.resume_error(RuntimeError("Promise already completed"))
            state = self._merge_task(state, task_id, error_state)
            scheduler.enqueue_ready(task_id, ResumeWithState(state))
            return state

        spawned_info.is_complete = True
        spawned_info.result = effect.value

        state = self._resume_join_waiters(
            state, handle_id, join_waiters, spawned_tasks, scheduler
        )

        new_single = suspended.resume(None, state.store)
        state = self._merge_task(state, task_id, new_single)
        scheduler.enqueue_ready(task_id, ResumeWithState(state))
        return state

    def _handle_fail_promise(
        self,
        state: CESKState,
        task_id: TaskId,
        effect: FailPromiseEffect,
        suspended: Suspended,
        user_promises: dict[Any, Promise[Any]],
        join_waiters: dict[Any, list[tuple[TaskId, Suspended]]],
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        scheduler: Scheduler,
    ) -> CESKState:
        promise = effect.promise
        handle_id = promise.future._handle

        if handle_id not in spawned_tasks:
            error_state = suspended.resume_error(
                ValueError(f"Invalid promise handle: {handle_id}")
            )
            state = self._merge_task(state, task_id, error_state)
            scheduler.enqueue_ready(task_id, ResumeWithState(state))
            return state

        spawned_info = spawned_tasks[handle_id]
        if spawned_info.is_complete:
            error_state = suspended.resume_error(RuntimeError("Promise already completed"))
            state = self._merge_task(state, task_id, error_state)
            scheduler.enqueue_ready(task_id, ResumeWithState(state))
            return state

        spawned_info.is_complete = True
        spawned_info.error = effect.error

        state = self._resume_join_waiters(
            state, handle_id, join_waiters, spawned_tasks, scheduler
        )

        new_single = suspended.resume(None, state.store)
        state = self._merge_task(state, task_id, new_single)
        scheduler.enqueue_ready(task_id, ResumeWithState(state))
        return state

    # ========== Gather/Race Effects ==========

    def _handle_gather_futures(
        self,
        state: CESKState,
        task_id: TaskId,
        futures: tuple[Future[Any], ...],
        suspended: Suspended,
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        gather_waiters: dict[TaskId, tuple[list[TaskId], Suspended]],
        gather_task_meta: dict[
            TaskId, tuple[tuple[Future[Any], ...], dict[TaskId, int]]
        ],
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

    def _check_gather_complete(
        self,
        state: CESKState,
        completed_id: TaskId,
        gather_waiters: dict[TaskId, tuple[list[TaskId], Suspended]],
        gather_task_meta: dict[
            TaskId, tuple[tuple[Future[Any], ...], dict[TaskId, int]]
        ],
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        task_id_to_handle: dict[TaskId, Any],
        scheduler: Scheduler,
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
                    state = self._merge_task(state, parent_id, error_state)
                    scheduler.enqueue_ready(parent_id, ResumeWithState(state))
                    return state

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
                state = self._merge_task(state, parent_id, new_single)
                scheduler.enqueue_ready(parent_id, ResumeWithState(state))
                return state

        return state

    def _handle_race_futures(
        self,
        state: CESKState,
        task_id: TaskId,
        futures: tuple[Future[Any], ...],
        suspended: Suspended,
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        race_waiters: dict[TaskId, tuple[list[TaskId], Suspended]],
        race_task_meta: dict[
            TaskId, tuple[tuple[Future[Any], ...], dict[TaskId, Future[Any]]]
        ],
        scheduler: Scheduler,
    ) -> CESKState:
        for future in futures:
            handle_id = future._handle
            if handle_id in spawned_tasks:
                spawned_info = spawned_tasks[handle_id]
                if spawned_info.is_complete:
                    if spawned_info.error is not None:
                        error_state = suspended.resume_error(spawned_info.error)
                        state = self._merge_task(state, task_id, error_state)
                        scheduler.enqueue_ready(task_id, ResumeWithState(state))
                        return state
                    rest = tuple(f for f in futures if f is not future)
                    race_result = RaceResult(
                        first=future, value=spawned_info.result, rest=rest
                    )
                    new_single = suspended.resume(race_result, state.store)
                    state = self._merge_task(state, task_id, new_single)
                    scheduler.enqueue_ready(task_id, ResumeWithState(state))
                    return state

        future_to_task_id = {
            spawned_tasks[f._handle].task_id: f
            for f in futures
            if f._handle in spawned_tasks
        }
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
        race_task_meta: dict[
            TaskId, tuple[tuple[Future[Any], ...], dict[TaskId, Future[Any]]]
        ],
        scheduler: Scheduler,
    ) -> CESKState:
        for parent_id, (child_ids, suspended) in list(race_waiters.items()):
            if completed_id not in child_ids:
                continue

            if completed_id in task_errors:
                del race_waiters[parent_id]
                race_task_meta.pop(parent_id, None)
                error_state = suspended.resume_error(task_errors[completed_id])
                error_state = self._fix_store_rollback(error_state, state.store)
                state = self._merge_task(state, parent_id, error_state)
                scheduler.enqueue_ready(parent_id, ResumeWithState(state))
                return state

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
                race_result = RaceResult(
                    first=winner, value=task_results[completed_id], rest=rest
                )
                new_single = suspended.resume(race_result, state.store)
                state = self._merge_task(state, parent_id, new_single)
                scheduler.enqueue_ready(parent_id, ResumeWithState(state))
                return state

        return state

    # ========== Async Effects (via ThreadedAsyncioExecutor) ==========

    def _handle_await(
        self,
        state: CESKState,
        task_id: TaskId,
        effect: FutureAwaitEffect,
        suspended: Suspended,
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        task_id_to_handle: dict[TaskId, Any],
        scheduler: Scheduler,
    ) -> None:
        executor = self._get_executor()

        def on_success(value: Any) -> None:
            scheduler.complete(task_id, value)

        def on_error(error: BaseException) -> None:
            scheduler.fail(task_id, error)

        scheduler.suspend_on(task_id, task_id, suspended)
        executor.submit(effect.awaitable, on_success, on_error)

    def _handle_delay(
        self,
        state: CESKState,
        task_id: TaskId,
        effect: DelayEffect,
        suspended: Suspended,
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        task_id_to_handle: dict[TaskId, Any],
        scheduler: Scheduler,
    ) -> None:
        import asyncio

        executor = self._get_executor()

        async def do_delay() -> None:
            await asyncio.sleep(effect.seconds)

        def on_success(value: Any) -> None:
            scheduler.complete(task_id, None)

        def on_error(error: BaseException) -> None:
            scheduler.fail(task_id, error)

        scheduler.suspend_on(task_id, task_id, suspended)
        executor.submit(do_delay(), on_success, on_error)

    def _handle_wait_until(
        self,
        state: CESKState,
        task_id: TaskId,
        effect: WaitUntilEffect,
        suspended: Suspended,
        spawned_tasks: dict[Any, SpawnedTaskInfo],
        task_id_to_handle: dict[TaskId, Any],
        scheduler: Scheduler,
    ) -> None:
        import asyncio

        executor = self._get_executor()

        async def do_wait_until() -> None:
            now = datetime.now()
            if effect.target_time > now:
                delay_seconds = (effect.target_time - now).total_seconds()
                await asyncio.sleep(delay_seconds)

        def on_success(value: Any) -> None:
            scheduler.complete(task_id, None)

        def on_error(error: BaseException) -> None:
            scheduler.fail(task_id, error)

        scheduler.suspend_on(task_id, task_id, suspended)
        executor.submit(do_wait_until(), on_success, on_error)


__all__ = [
    "SyncRuntime",
]
