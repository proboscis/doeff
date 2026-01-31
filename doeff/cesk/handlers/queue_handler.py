"""Queue handler for cooperative scheduling - manages task queue using store primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from doeff._types_internal import EffectBase
from doeff.cesk.frames import ContinueValue, FrameResult
from doeff.cesk.handler_frame import HandlerContext
from doeff.effects.queue import (
    CancelTask,
    CreatePromiseHandle,
    CreateTaskHandle,
    GetCurrentTaskId,
    GetCurrentTaskStore,
    GetTaskResult,
    IsTaskDone,
    QueueAdd,
    QueueIsEmpty,
    QueuePop,
    RegisterWaiter,
    SetTaskSuspended,
    TaskComplete,
    UpdateTaskStore,
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
PENDING_IO_KEY = "__scheduler_pending_io__"


SCHEDULER_KEY_PREFIX = "__scheduler_"


def _ensure_scheduler_store_initialized(store: dict[str, Any]) -> None:
    """Lazily initialize core scheduler store keys if not present.
    
    Initializes: TASK_QUEUE_KEY, TASK_REGISTRY_KEY, WAITERS_KEY, CURRENT_TASK_KEY.
    Does NOT initialize PENDING_IO_KEY or TASK_SUSPENDED_KEY (set on-demand by effects).
    """
    if TASK_QUEUE_KEY not in store:
        store[TASK_QUEUE_KEY] = []
    if TASK_REGISTRY_KEY not in store:
        store[TASK_REGISTRY_KEY] = {}
    if WAITERS_KEY not in store:
        store[WAITERS_KEY] = {}
    if CURRENT_TASK_KEY not in store:
        store[CURRENT_TASK_KEY] = uuid4()


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


def queue_handler(effect: EffectBase, ctx: HandlerContext) -> Program[FrameResult]:
    """Handle queue effects using store primitives.
    
    This is the outermost handler in the handler stack. It manages:
    - Task queue (ready tasks waiting to run)
    - Task registry (tracking spawned tasks and their results)
    - Waiters (tasks waiting for other tasks to complete)
    """
    store = dict(ctx.store)
    _ensure_scheduler_store_initialized(store)
    
    if isinstance(effect, QueueAdd):
        queue = list(store.get(TASK_QUEUE_KEY, []))
        queue.append({
            "task_id": effect.task_id,
            "k": effect.k,
            "store_snapshot": effect.store_snapshot,
            "program": effect.program,
        })
        store[TASK_QUEUE_KEY] = queue
        return Program.pure(ContinueValue(
            value=None,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    if isinstance(effect, QueuePop):
        import os
        debug = os.environ.get("DOEFF_DEBUG")
        queue = list(store.get(TASK_QUEUE_KEY, []))
        if debug:
            print(f"[QueuePop] queue_len={len(queue)}")
        if queue:
            item = queue.pop(0)
            store[TASK_QUEUE_KEY] = queue
            if debug:
                print(f"[QueuePop] Returning task_id={item['task_id']}, resume_value={item.get('resume_value')}")
            return Program.pure(ContinueValue(
                value=(
                    item["task_id"],
                    item["k"],
                    item.get("store_snapshot"),
                    item.get("resume_value"),
                    item.get("resume_error"),
                    dict(store),
                ),
                env=ctx.env,
                store=store,
                k=ctx.delimited_k,
            ))
        if debug:
            print(f"[QueuePop] Queue empty! Returning None")
        return Program.pure(ContinueValue(
            value=None,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    if isinstance(effect, QueueIsEmpty):
        queue = store.get(TASK_QUEUE_KEY, [])
        return Program.pure(ContinueValue(
            value=len(queue) == 0,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    if isinstance(effect, RegisterWaiter):
        waiters = dict(store.get(WAITERS_KEY, {}))
        handle_id = effect.handle_id
        if handle_id not in waiters:
            waiters[handle_id] = []
        waiters[handle_id] = list(waiters[handle_id])
        waiters[handle_id].append({
            "waiter_task_id": effect.waiter_task_id,
            "waiter_k": effect.waiter_k,
            "waiter_store": effect.waiter_store,
        })
        store[WAITERS_KEY] = waiters
        return Program.pure(ContinueValue(
            value=None,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    if isinstance(effect, CreateTaskHandle):
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
        return Program.pure(ContinueValue(
            value=(handle_id, task_handle),
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    if isinstance(effect, TaskComplete):
        import os
        debug = os.environ.get("DOEFF_DEBUG")
        
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
            print(f"[TaskComplete] handle={handle_id}, result={effect.result}, waiters_for_handle={handle_id in waiters}, waiter_task_ids={all_waiter_task_ids}")
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
                        print(f"[TaskComplete] Skipping duplicate waiter for task_id={waiter_task_id}")
                    continue
                queue.append({
                    "task_id": waiter_task_id,
                    "k": waiter["waiter_k"],
                    "store_snapshot": waiter.get("waiter_store"),
                    "resume_value": effect.result,
                    "resume_error": effect.error,
                })
                existing_task_ids.add(waiter_task_id)
                added += 1
            if debug:
                print(f"[TaskComplete] Added {added} waiters to queue (skipped {len(waiting_list) - added}), queue_len now={len(queue)}")
            store[TASK_QUEUE_KEY] = queue
        
        return Program.pure(ContinueValue(
            value=None,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    if isinstance(effect, GetTaskResult):
        registry = store.get(TASK_REGISTRY_KEY, {})
        handle_id = effect.handle_id
        
        if handle_id not in registry:
            return Program.pure(ContinueValue(
                value=None,
                env=ctx.env,
                store=store,
                k=ctx.delimited_k,
            ))
        
        task_info = registry[handle_id]
        return Program.pure(ContinueValue(
            value=(task_info.is_complete, task_info.is_cancelled, task_info.result, task_info.error),
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    if isinstance(effect, CancelTask):
        registry = dict(store.get(TASK_REGISTRY_KEY, {}))
        handle_id = effect.handle_id
        
        if handle_id not in registry:
            return Program.pure(ContinueValue(
                value=False,
                env=ctx.env,
                store=store,
                k=ctx.delimited_k,
            ))
        
        task_info = registry[handle_id]
        if task_info.is_complete:
            return Program.pure(ContinueValue(
                value=False,
                env=ctx.env,
                store=store,
                k=ctx.delimited_k,
            ))
        
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
                queue.append({
                    "task_id": waiter["waiter_task_id"],
                    "k": waiter["waiter_k"],
                    "store_snapshot": None,
                })
            store[TASK_QUEUE_KEY] = queue
        
        return Program.pure(ContinueValue(
            value=True,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    if isinstance(effect, IsTaskDone):
        registry = store.get(TASK_REGISTRY_KEY, {})
        handle_id = effect.handle_id
        
        if handle_id not in registry:
            return Program.pure(ContinueValue(
                value=True,
                env=ctx.env,
                store=store,
                k=ctx.delimited_k,
            ))
        
        task_info = registry[handle_id]
        return Program.pure(ContinueValue(
            value=task_info.is_complete,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    if isinstance(effect, CreatePromiseHandle):
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
        
        return Program.pure(ContinueValue(
            value=(handle_id, promise),
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    if isinstance(effect, GetCurrentTaskId):
        current = store.get(CURRENT_TASK_KEY)
        return Program.pure(ContinueValue(
            value=current,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    if isinstance(effect, GetCurrentTaskStore):
        registry = store.get(TASK_REGISTRY_KEY, {})
        for task_info in registry.values():
            if task_info.task_id == effect.task_id:
                return Program.pure(ContinueValue(
                    value=task_info.store_snapshot,
                    env=ctx.env,
                    store=store,
                    k=ctx.delimited_k,
                ))
        return Program.pure(ContinueValue(
            value=None,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    if isinstance(effect, UpdateTaskStore):
        registry = dict(store.get(TASK_REGISTRY_KEY, {}))
        for handle_id, task_info in registry.items():
            if task_info.task_id == effect.task_id:
                task_info.store_snapshot = effect.store
                registry[handle_id] = task_info
                break
        store[TASK_REGISTRY_KEY] = registry
        return Program.pure(ContinueValue(
            value=None,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    if isinstance(effect, SetTaskSuspended):
        store[TASK_SUSPENDED_KEY] = {
            "task_id": effect.task_id,
            "waiting_for": effect.waiting_for,
        }
        return Program.pure(ContinueValue(
            value=None,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    from doeff.effects.queue import AddPendingIO, GetPendingIO, RemovePendingIO, ResumePendingIO
    
    if isinstance(effect, AddPendingIO):
        pending = dict(store.get(PENDING_IO_KEY, {}))
        pending[effect.task_id] = {
            "awaitable": effect.awaitable,
            "k": effect.k,
            "store_snapshot": effect.store_snapshot,
        }
        store[PENDING_IO_KEY] = pending
        return Program.pure(ContinueValue(
            value=None,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    if isinstance(effect, GetPendingIO):
        pending = store.get(PENDING_IO_KEY, {})
        return Program.pure(ContinueValue(
            value=pending,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    if isinstance(effect, RemovePendingIO):
        pending = dict(store.get(PENDING_IO_KEY, {}))
        pending.pop(effect.task_id, None)
        store[PENDING_IO_KEY] = pending
        return Program.pure(ContinueValue(
            value=None,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    if isinstance(effect, ResumePendingIO):
        pending = dict(store.get(PENDING_IO_KEY, {}))
        task_info = pending.pop(effect.task_id, None)
        store[PENDING_IO_KEY] = pending
        if task_info is not None:
            queue = list(store.get(TASK_QUEUE_KEY, []))
            queue.append({
                "task_id": effect.task_id,
                "k": task_info["k"],
                "store_snapshot": task_info["store_snapshot"],
                "resume_value": effect.value,
                "resume_error": effect.error,
            })
            store[TASK_QUEUE_KEY] = queue
        return Program.pure(ContinueValue(
            value=None,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        ))
    
    from doeff.cesk.errors import UnhandledEffectError
    raise UnhandledEffectError(f"queue_handler: unhandled effect {type(effect).__name__}")


__all__ = [
    "CURRENT_TASK_KEY",
    "PENDING_IO_KEY",
    "SCHEDULER_KEY_PREFIX",
    "TASK_QUEUE_KEY",
    "TASK_REGISTRY_KEY",
    "TASK_SUSPENDED_KEY",
    "TaskInfo",
    "WAITERS_KEY",
    "queue_handler",
]
