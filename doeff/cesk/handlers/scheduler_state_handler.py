"""Scheduler state handler for cooperative scheduling - manages task queue using store primitives."""

from __future__ import annotations

import queue
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from doeff._types_internal import EffectBase
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.state import CESKState
from doeff.effects.external_promise import (
    CreateExternalPromiseEffect,
    ExternalPromise,
)
from doeff.effects.scheduler_internal import (
    _SchedulerCancelTask,
    _SchedulerCreatePromise,
    _SchedulerCreateTaskHandle,
    _SchedulerDequeueTask,
    _SchedulerEnqueueTask,
    _SchedulerGetCurrentTaskId,
    _SchedulerGetTaskResult,
    _SchedulerGetTaskStore,
    _SchedulerIsTaskDone,
    _SchedulerQueueEmpty,
    _SchedulerRegisterWaiter,
    _SchedulerSetTaskSuspended,
    _SchedulerTaskComplete,
    _SchedulerUpdateTaskStore,
)
from doeff.effects.spawn import Promise, Task, TaskCancelledError
from doeff.program import Program

if TYPE_CHECKING:
    pass


TASK_QUEUE_KEY = "__scheduler_queue__"
TASK_REGISTRY_KEY = "__scheduler_tasks__"
WAITERS_KEY = "__scheduler_waiters__"
CURRENT_TASK_KEY = "__scheduler_current_task__"
TASK_SUSPENDED_KEY = "__scheduler_task_suspended__"
EXTERNAL_COMPLETION_QUEUE_KEY = "__scheduler_external_queue__"
EXTERNAL_PROMISE_REGISTRY_KEY = "__scheduler_external_promises__"
SPAWN_ASYNC_HANDLER_KEY = "__scheduler_spawn_async_handler__"


def _ensure_scheduler_store_initialized(store: dict[str, Any]) -> None:
    """Lazily initialize scheduler store keys if not present.

    This allows handlers to manage their own store structure without
    requiring runtimes to pre-initialize handler-specific keys.
    """
    if TASK_QUEUE_KEY not in store:
        store[TASK_QUEUE_KEY] = []
    if TASK_REGISTRY_KEY not in store:
        store[TASK_REGISTRY_KEY] = {}
    if WAITERS_KEY not in store:
        store[WAITERS_KEY] = {}
    if CURRENT_TASK_KEY not in store:
        store[CURRENT_TASK_KEY] = uuid4()
    if EXTERNAL_COMPLETION_QUEUE_KEY not in store:
        store[EXTERNAL_COMPLETION_QUEUE_KEY] = queue.Queue()
    if EXTERNAL_PROMISE_REGISTRY_KEY not in store:
        store[EXTERNAL_PROMISE_REGISTRY_KEY] = {}


@dataclass
class TaskInfo:
    task_id: Any
    handle_id: Any
    env_snapshot: dict[Any, Any]
    store_snapshot: dict[str, Any]
    is_complete: bool = False
    is_cancelled: bool = False
    result: Any = None
    error: BaseException | None = None


def scheduler_state_handler(effect: EffectBase, ctx: HandlerContext) -> Program[CESKState]:
    """Handle scheduler state effects using store primitives.

    This is the outermost handler in the handler stack. It manages:
    - Task queue (ready tasks waiting to run)
    - Task registry (tracking spawned tasks and their results)
    - Waiters (tasks waiting for other tasks to complete)
    """
    store = dict(ctx.store)
    _ensure_scheduler_store_initialized(store)

    if isinstance(effect, _SchedulerEnqueueTask):
        queue = list(store.get(TASK_QUEUE_KEY, []))
        queue.append(
            {
                "task_id": effect.task_id,
                "k": effect.k,
                "store_snapshot": effect.store_snapshot,
                "program": effect.program,
            }
        )
        store[TASK_QUEUE_KEY] = queue
        return Program.pure(CESKState.with_value(None, ctx.env, store, ctx.k))

    if isinstance(effect, _SchedulerDequeueTask):
        queue = list(store.get(TASK_QUEUE_KEY, []))
        if queue:
            item = queue.pop(0)
            store[TASK_QUEUE_KEY] = queue
            return Program.pure(CESKState.with_value(
                (
                    item["task_id"],
                    item["k"],
                    item.get("store_snapshot"),
                    item.get("resume_value"),
                    item.get("resume_error"),
                    dict(store),
                ),
                ctx.env, store, ctx.k,
            ))
        # Return (None, current_store) so handlers can access updated scheduler state
        # even when there's no task to dequeue
        return Program.pure(CESKState.with_value((None, dict(store)), ctx.env, store, ctx.k))

    if isinstance(effect, _SchedulerQueueEmpty):
        queue = store.get(TASK_QUEUE_KEY, [])
        return Program.pure(CESKState.with_value(len(queue) == 0, ctx.env, store, ctx.k))

    if isinstance(effect, _SchedulerRegisterWaiter):
        waiters = dict(store.get(WAITERS_KEY, {}))
        handle_id = effect.handle_id
        if handle_id not in waiters:
            waiters[handle_id] = []
        waiters[handle_id] = list(waiters[handle_id])
        waiters[handle_id].append(
            {
                "waiter_task_id": effect.waiter_task_id,
                "waiter_k": effect.waiter_k,
                "waiter_store": effect.waiter_store,
            }
        )
        store[WAITERS_KEY] = waiters
        return Program.pure(CESKState.with_value(None, ctx.env, store, ctx.k))

    if isinstance(effect, _SchedulerCreateTaskHandle):
        handle_id = uuid4()
        task_info = TaskInfo(
            task_id=effect.task_id,
            handle_id=handle_id,
            env_snapshot=effect.env_snapshot,
            store_snapshot=effect.store_snapshot,
        )
        registry = dict(store.get(TASK_REGISTRY_KEY, {}))
        registry[handle_id] = task_info
        store[TASK_REGISTRY_KEY] = registry

        task_handle = Task(
            backend="thread",
            _handle=handle_id,
            _env_snapshot=effect.env_snapshot,
            _state_snapshot=effect.store_snapshot,
        )
        return Program.pure(CESKState.with_value((handle_id, task_handle), ctx.env, store, ctx.k))

    if isinstance(effect, _SchedulerTaskComplete):
        import os

        debug = os.environ.get("DOEFF_DEBUG", "").lower() in ("1", "true", "yes")

        registry = dict(store.get(TASK_REGISTRY_KEY, {}))
        handle_id = effect.handle_id

        if handle_id in registry:
            task_info = registry[handle_id]
            task_info.is_complete = True
            task_info.result = effect.result
            task_info.error = effect.error
            if effect.store_snapshot is not None:
                task_info.store_snapshot = effect.store_snapshot
            registry[handle_id] = task_info
            store[TASK_REGISTRY_KEY] = registry

        waiters = dict(store.get(WAITERS_KEY, {}))
        if debug:
            all_waiter_task_ids = []
            if handle_id in waiters:
                all_waiter_task_ids = [w["waiter_task_id"] for w in waiters[handle_id]]
            print(
                f"[_SchedulerTaskComplete] handle={handle_id}, result={effect.result}, error={effect.error}, waiters_for_handle={handle_id in waiters}, waiter_task_ids={all_waiter_task_ids}"
            )
        if handle_id in waiters:
            waiting_list = waiters.pop(handle_id)
            store[WAITERS_KEY] = waiters

            queue = list(store.get(TASK_QUEUE_KEY, []))
            existing_task_ids = {item["task_id"] for item in queue}

            added = 0
            for waiter in waiting_list:
                waiter_task_id = waiter["waiter_task_id"]
                if waiter_task_id in existing_task_ids:
                    if debug:
                        print(
                            f"[_SchedulerTaskComplete] Skipping duplicate waiter for task_id={waiter_task_id}"
                        )
                    continue
                queue.append(
                    {
                        "task_id": waiter_task_id,
                        "k": waiter["waiter_k"],
                        "store_snapshot": waiter.get("waiter_store"),
                        "resume_value": effect.result,
                        "resume_error": effect.error,
                    }
                )
                existing_task_ids.add(waiter_task_id)
                added += 1
            if debug:
                print(
                    f"[_SchedulerTaskComplete] Added {added} waiters to queue (skipped {len(waiting_list) - added}), queue_len now={len(queue)}"
                )
            store[TASK_QUEUE_KEY] = queue

        return Program.pure(CESKState.with_value(None, ctx.env, store, ctx.k))

    if isinstance(effect, _SchedulerGetTaskResult):
        registry = store.get(TASK_REGISTRY_KEY, {})
        handle_id = effect.handle_id

        if handle_id not in registry:
            return Program.pure(CESKState.with_value(None, ctx.env, store, ctx.k))

        task_info = registry[handle_id]
        return Program.pure(CESKState.with_value(
            (task_info.is_complete, task_info.is_cancelled, task_info.result, task_info.error),
            ctx.env, store, ctx.k,
        ))

    if isinstance(effect, _SchedulerCancelTask):
        registry = dict(store.get(TASK_REGISTRY_KEY, {}))
        handle_id = effect.handle_id

        if handle_id not in registry:
            return Program.pure(CESKState.with_value(False, ctx.env, store, ctx.k))

        task_info = registry[handle_id]
        if task_info.is_complete:
            return Program.pure(CESKState.with_value(False, ctx.env, store, ctx.k))

        task_info.is_complete = True
        task_info.is_cancelled = True
        task_info.error = TaskCancelledError()
        registry[handle_id] = task_info
        store[TASK_REGISTRY_KEY] = registry

        waiters = dict(store.get(WAITERS_KEY, {}))
        if handle_id in waiters:
            waiting_list = waiters.pop(handle_id)
            store[WAITERS_KEY] = waiters

            queue = list(store.get(TASK_QUEUE_KEY, []))
            for waiter in waiting_list:
                queue.append(
                    {
                        "task_id": waiter["waiter_task_id"],
                        "k": waiter["waiter_k"],
                        "store_snapshot": None,
                    }
                )
            store[TASK_QUEUE_KEY] = queue

        return Program.pure(CESKState.with_value(True, ctx.env, store, ctx.k))

    if isinstance(effect, _SchedulerIsTaskDone):
        registry = store.get(TASK_REGISTRY_KEY, {})
        handle_id = effect.handle_id

        if handle_id not in registry:
            return Program.pure(CESKState.with_value(True, ctx.env, store, ctx.k))

        task_info = registry[handle_id]
        return Program.pure(CESKState.with_value(task_info.is_complete, ctx.env, store, ctx.k))

    if isinstance(effect, _SchedulerCreatePromise):
        handle_id = uuid4()
        task_info = TaskInfo(
            task_id=None,
            handle_id=handle_id,
            env_snapshot={},
            store_snapshot={},
            is_complete=False,
        )
        registry = dict(store.get(TASK_REGISTRY_KEY, {}))
        registry[handle_id] = task_info
        store[TASK_REGISTRY_KEY] = registry

        promise = Promise(_promise_handle=handle_id)

        return Program.pure(CESKState.with_value((handle_id, promise), ctx.env, store, ctx.k))

    if isinstance(effect, CreateExternalPromiseEffect):
        # Create handle (same as regular promise)
        handle_id = uuid4()
        task_info = TaskInfo(
            task_id=None,
            handle_id=handle_id,
            env_snapshot={},
            store_snapshot={},
            is_complete=False,
        )
        registry = dict(store.get(TASK_REGISTRY_KEY, {}))
        registry[handle_id] = task_info
        store[TASK_REGISTRY_KEY] = registry

        # Create unique promise ID
        promise_id = uuid4()

        # Register in external promise registry (maps promise_id -> handle_id)
        external_registry = dict(store.get(EXTERNAL_PROMISE_REGISTRY_KEY, {}))
        external_registry[promise_id] = handle_id
        store[EXTERNAL_PROMISE_REGISTRY_KEY] = external_registry

        # Get the completion queue
        completion_queue = store.get(EXTERNAL_COMPLETION_QUEUE_KEY)

        # Create ExternalPromise with queue reference
        external_promise = ExternalPromise(
            _handle=handle_id,
            _completion_queue=completion_queue,
            _id=promise_id,
        )

        return Program.pure(CESKState.with_value(external_promise, ctx.env, store, ctx.k))

    if isinstance(effect, _SchedulerGetCurrentTaskId):
        current = store.get(CURRENT_TASK_KEY)
        return Program.pure(CESKState.with_value(current, ctx.env, store, ctx.k))

    if isinstance(effect, _SchedulerGetTaskStore):
        registry = store.get(TASK_REGISTRY_KEY, {})
        for task_info in registry.values():
            if task_info.task_id == effect.task_id:
                return Program.pure(CESKState.with_value(task_info.store_snapshot, ctx.env, store, ctx.k))
        return Program.pure(CESKState.with_value(None, ctx.env, store, ctx.k))

    if isinstance(effect, _SchedulerUpdateTaskStore):
        registry = dict(store.get(TASK_REGISTRY_KEY, {}))
        for handle_id, task_info in registry.items():
            if task_info.task_id == effect.task_id:
                task_info.store_snapshot = effect.store
                registry[handle_id] = task_info
                break
        store[TASK_REGISTRY_KEY] = registry
        return Program.pure(CESKState.with_value(None, ctx.env, store, ctx.k))

    if isinstance(effect, _SchedulerSetTaskSuspended):
        store[TASK_SUSPENDED_KEY] = {
            "task_id": effect.task_id,
            "waiting_for": effect.waiting_for,
        }
        return Program.pure(CESKState.with_value(None, ctx.env, store, ctx.k))

    from doeff.cesk.errors import UnhandledEffectError

    raise UnhandledEffectError(f"scheduler_state_handler: unhandled effect {type(effect).__name__}")


def process_external_completions(store: dict[str, Any]) -> int:
    """Process any pending external promise completions.

    Checks the external completion queue (non-blocking) and wakes up
    waiters for any completed external promises.

    This should be called during the step loop to handle completions
    from external code (threads, asyncio, etc.).

    Args:
        store: The CESK store containing scheduler state

    Returns:
        Number of completions processed
    """
    _ensure_scheduler_store_initialized(store)

    completion_queue = store.get(EXTERNAL_COMPLETION_QUEUE_KEY)
    # IMPORTANT: Copy the registry before modifying to avoid aliasing issues.
    # Multiple stores may share the same registry dict reference (shallow copies),
    # so we need to copy before .pop() to avoid affecting other stores.
    external_registry = dict(store.get(EXTERNAL_PROMISE_REGISTRY_KEY, {}))
    task_registry = dict(store.get(TASK_REGISTRY_KEY, {}))
    waiters = dict(store.get(WAITERS_KEY, {}))
    task_queue = list(store.get(TASK_QUEUE_KEY, []))

    processed = 0

    while not completion_queue.empty():
        try:
            promise_id, value, error = completion_queue.get_nowait()
        except queue.Empty:
            break

        # Look up the handle_id for this promise
        handle_id = external_registry.get(promise_id)
        if handle_id is None:
            # Unknown promise ID - skip
            continue

        # Mark the promise as complete in the registry
        task_info = task_registry.get(handle_id)
        if task_info is not None and not task_info.is_complete:
            task_info.is_complete = True
            task_info.result = value
            task_info.error = error
            task_registry[handle_id] = task_info

            # Wake up any waiters
            waiting_list = waiters.pop(handle_id, [])
            for waiter in waiting_list:
                task_queue.append({
                    "task_id": waiter["waiter_task_id"],
                    "k": waiter["waiter_k"],
                    "store_snapshot": waiter.get("waiter_store"),
                    "resume_value": value,
                    "resume_error": error,
                })

            processed += 1

        # Remove from external registry (one-time use)
        external_registry.pop(promise_id, None)

    # Update store
    store[TASK_REGISTRY_KEY] = task_registry
    store[WAITERS_KEY] = waiters
    store[TASK_QUEUE_KEY] = task_queue
    store[EXTERNAL_PROMISE_REGISTRY_KEY] = external_registry

    return processed


# Backwards compatibility alias (deprecated)
queue_handler = scheduler_state_handler


__all__ = [
    "CURRENT_TASK_KEY",
    "EXTERNAL_COMPLETION_QUEUE_KEY",
    "EXTERNAL_PROMISE_REGISTRY_KEY",
    "TASK_QUEUE_KEY",
    "TASK_REGISTRY_KEY",
    "TASK_SUSPENDED_KEY",
    "TaskInfo",
    "WAITERS_KEY",
    "process_external_completions",
    "queue_handler",
    "scheduler_state_handler",
]
