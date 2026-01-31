"""Scheduler handler for cooperative multi-task scheduling.

Pure handler approach: all task scheduling happens through handlers.
No task tracking in outer runtime - just step until Done/Failed.

## Architecture Notes

### P2: FutureAwaitEffect and DelayEffect Handling (ISSUE-CORE-467)

The scheduler_handler currently handles FutureAwaitEffect and DelayEffect directly
using blocking I/O operations (asyncio.run and time.sleep). This is intentional
for the SyncRuntime use case but has limitations:

- **Trade-off**: Simplifies the synchronous runtime at the cost of blocking
- **Alternative for async**: Use AsyncRuntime which handles these natively
- **Future improvement**: Could be extracted to a separate io_handler or made
  configurable via dependency injection

The current implementation is acceptable for:
- Synchronous scripts where blocking is acceptable
- Tests that don't require true parallelism
- Simple use cases without complex async coordination

For production async workloads, use AsyncRuntime instead.

### P3: Lazy Imports (ISSUE-CORE-467)

Several imports are performed inside function bodies to avoid circular imports:
- GatherWaiterFrame (line ~397)
- RaceWaiterFrame (line ~491)
- FutureAwaitEffect, DelayEffect (line ~529-530)

This is a documented design choice:
- Frame types depend on effect types
- Effect types may depend on handler types
- Breaking this cycle at runtime is intentional

The lazy import pattern is stable and doesn't impact performance significantly
since these imports only happen once per effect type encountered.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from doeff._vendor import FrozenDict
from doeff.do import do
from doeff._types_internal import EffectBase
from doeff.cesk.frames import ContinueError, ContinueValue, FrameResult, ReturnFrame
from doeff.cesk.handler_frame import Handler, HandlerContext, ResumeK, WithHandler
from doeff.cesk.helpers import to_generator
from doeff.cesk.state import ProgramControl
from doeff.effects.gather import GatherEffect
from doeff.effects.promise import (
    CompletePromiseEffect,
    CreatePromiseEffect,
    FailPromiseEffect,
)
from doeff.cesk.handlers.queue_handler import (
    CURRENT_TASK_KEY,
    PENDING_IO_KEY,
    TASK_QUEUE_KEY,
    TASK_REGISTRY_KEY,
    WAITERS_KEY,
)
from doeff.effects.queue import (
    CancelTask,
    CreatePromiseHandle,
    CreateTaskHandle,
    GetCurrentTaskId,
    GetTaskResult,
    IsTaskDone,
    QueueAdd,
    QueuePop,
    RegisterWaiter,
    TaskComplete,
    TaskCompletedEffect,
)
from doeff.effects.race import RaceEffect, RaceResult
from doeff.effects.spawn import (
    SpawnEffect,
    Task,
    TaskCancelEffect,
    TaskCancelledError,
    TaskIsDoneEffect,
    Waitable,
)
from doeff.effects.wait import WaitEffect
from doeff.program import Program

if TYPE_CHECKING:
    pass


def _make_spawn_wrapper(program: Any, task_id: Any, handle_id: Any) -> Program[Any]:
    """Wrap spawned program to yield TaskCompletedEffect on completion."""
    @do
    def wrapper():
        try:
            result = yield program
            yield TaskCompletedEffect(task_id=task_id, handle_id=handle_id, result=result)
        except Exception as e:
            yield TaskCompletedEffect(task_id=task_id, handle_id=handle_id, error=e)
    return wrapper()


def _wrap_with_handlers_for_spawn(program: Any, task_id: Any, handle_id: Any) -> Any:
    """Wrap a spawned program with handlers and completion wrapper.
    
    Note: We do NOT wrap with queue_handler here. The queue_handler manages
    global scheduler state (queue, registry, waiters) that must be shared
    across all tasks. Effects from spawned tasks bubble up to the main
    queue_handler through the continuation stack.
    """
    from doeff.cesk.handlers.async_effects_handler import async_effects_handler
    from doeff.cesk.handlers.core_handler import core_handler
    
    wrapped = _make_spawn_wrapper(program, task_id, handle_id)
    
    return WithHandler(
        handler=cast(Handler, scheduler_handler),
        program=WithHandler(
            handler=cast(Handler, async_effects_handler),
            program=WithHandler(
                handler=cast(Handler, core_handler),
                program=wrapped,
            ),
        ),
    )


def _make_initial_k(program: Any, env: Any = None) -> list[Any]:
    """Create initial continuation for a program."""
    gen = to_generator(program)
    saved_env = FrozenDict(env) if env else FrozenDict()
    return [ReturnFrame(gen, saved_env)]


@do  # type: ignore[reportArgumentType] - @do transforms generator to KleisliProgram
def scheduler_handler(effect: EffectBase, ctx: HandlerContext):
    """Handle scheduling effects using ResumeK for task switching."""
    
    if isinstance(effect, SpawnEffect):
        task_id = uuid4()
        env_snapshot = dict(ctx.env)
        store_snapshot = dict(ctx.store)
        store_snapshot[CURRENT_TASK_KEY] = task_id
        
        handle_id, task_handle = yield CreateTaskHandle(
            task_id=task_id,
            env_snapshot=env_snapshot,
            store_snapshot=store_snapshot,
        )
        
        wrapped_program = _wrap_with_handlers_for_spawn(effect.program, task_id, handle_id)
        child_k = _make_initial_k(wrapped_program, env_snapshot)
        yield QueueAdd(task_id=task_id, k=child_k, store_snapshot=store_snapshot)
        
        return ContinueValue(
            value=task_handle,
            env=ctx.env,
            store=None,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, TaskCompletedEffect):
        yield TaskComplete(
            handle_id=effect.handle_id,
            task_id=effect.task_id,
            result=effect.result,
            error=effect.error,
        )
        
        next_task = yield QueuePop()
        if next_task is None:
            pending_io = ctx.store.get(PENDING_IO_KEY, {})
            if pending_io:
                from doeff.cesk.frames import SuspendOn
                return SuspendOn(
                    awaitable=None,
                    stored_k=ctx.delimited_k,
                    stored_env=ctx.env,
                    stored_store=ctx.store,
                )
            if effect.error is not None:
                return ContinueError(
                    error=effect.error,
                    env=ctx.env,
                    store=ctx.store,
                    k=[],
                )
            return ContinueValue(
                value=effect.result,
                env=ctx.env,
                store=ctx.store,
                k=[],
            )
        
        next_task_id, next_k, next_store, resume_value, resume_error, current_store = next_task
        task_store = dict(next_store) if next_store is not None else {}
        for key, val in current_store.items():
            if isinstance(key, str) and key.startswith("__scheduler_"):
                task_store[key] = val
        task_store[CURRENT_TASK_KEY] = next_task_id
        if resume_error is not None:
            return ContinueError(
                error=resume_error,
                env=ctx.env,
                store=task_store,
                k=next_k,
            )
        return ResumeK(k=next_k, value=resume_value, store=task_store)
    
    if isinstance(effect, WaitEffect):
        waitable = effect.future
        if not isinstance(waitable, Waitable):
            return ContinueError(
                error=TypeError(f"Wait requires Waitable, got {type(waitable).__name__}"),
                env=ctx.env,
                store=ctx.store,
                k=ctx.delimited_k,
            )
        
        handle_id = waitable._handle
        result = yield GetTaskResult(handle_id=handle_id)
        
        if result is None:
            return ContinueError(
                error=ValueError(f"Invalid task handle: {handle_id}"),
                env=ctx.env,
                store=ctx.store,
                k=ctx.delimited_k,
            )
        
        is_complete, is_cancelled, task_result, task_error = result
        
        if is_complete:
            if is_cancelled:
                return ContinueError(
                    error=TaskCancelledError(),
                    env=ctx.env,
                    store=ctx.store,
                    k=ctx.delimited_k,
                )
            if task_error is not None:
                return ContinueError(
                    error=task_error,
                    env=ctx.env,
                    store=ctx.store,
                    k=ctx.delimited_k,
                )
            return ContinueValue(
                value=task_result,
                env=ctx.env,
                store=ctx.store,
                k=ctx.delimited_k,
            )
        
        current_task_id = yield GetCurrentTaskId()
        yield RegisterWaiter(
            handle_id=handle_id,
            waiter_task_id=current_task_id,
            waiter_k=list(ctx.delimited_k),
            waiter_store=dict(ctx.store),
        )
        
        next_task = yield QueuePop()
        if next_task is None:
            pending_io = ctx.store.get(PENDING_IO_KEY, {})
            if pending_io:
                from doeff.cesk.frames import SuspendOn
                return SuspendOn(
                    awaitable=None,
                    stored_k=list(ctx.delimited_k),
                    stored_env=ctx.env,
                    stored_store=ctx.store,
                )
            return ContinueError(
                error=RuntimeError("Deadlock: waiting for task but no other tasks to run"),
                env=ctx.env,
                store=ctx.store,
                k=ctx.delimited_k,
            )
        
        next_task_id, next_k, next_store, resume_value, resume_error, current_store = next_task
        task_store = dict(next_store) if next_store is not None else {}
        for key, val in current_store.items():
            if isinstance(key, str) and key.startswith("__scheduler_"):
                task_store[key] = val
        task_store[CURRENT_TASK_KEY] = next_task_id
        if resume_error is not None:
            return ContinueError(
                error=resume_error,
                env=ctx.env,
                store=task_store,
                k=next_k,
            )
        return ResumeK(k=next_k, value=resume_value, store=task_store)
    
    if isinstance(effect, TaskCancelEffect):
        handle_id = effect.task._handle
        was_cancelled = yield CancelTask(handle_id=handle_id)
        return ContinueValue(
            value=was_cancelled,
            env=ctx.env,
            store=ctx.store,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, TaskIsDoneEffect):
        handle_id = effect.task._handle
        is_done = yield IsTaskDone(handle_id=handle_id)
        return ContinueValue(
            value=is_done,
            env=ctx.env,
            store=ctx.store,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, CreatePromiseEffect):
        handle_id, promise = yield CreatePromiseHandle()
        return ContinueValue(
            value=promise,
            env=ctx.env,
            store=None,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, CompletePromiseEffect):
        handle_id = effect.promise.future._handle
        result = yield GetTaskResult(handle_id=handle_id)
        
        if result is None:
            return ContinueError(
                error=ValueError(f"Invalid promise handle: {handle_id}"),
                env=ctx.env,
                store=None,
                k=ctx.delimited_k,
            )
        
        is_complete, _, _, _ = result
        if is_complete:
            return ContinueError(
                error=RuntimeError("Promise already completed"),
                env=ctx.env,
                store=None,
                k=ctx.delimited_k,
            )
        
        yield TaskComplete(
            handle_id=handle_id,
            task_id=None,
            result=effect.value,
            error=None,
        )
        
        return ContinueValue(
            value=None,
            env=ctx.env,
            store=None,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, FailPromiseEffect):
        handle_id = effect.promise.future._handle
        result = yield GetTaskResult(handle_id=handle_id)
        
        if result is None:
            return ContinueError(
                error=ValueError(f"Invalid promise handle: {handle_id}"),
                env=ctx.env,
                store=None,
                k=ctx.delimited_k,
            )
        
        is_complete, _, _, _ = result
        if is_complete:
            return ContinueError(
                error=RuntimeError("Promise already completed"),
                env=ctx.env,
                store=None,
                k=ctx.delimited_k,
            )
        
        yield TaskComplete(
            handle_id=handle_id,
            task_id=None,
            result=None,
            error=effect.error,
        )
        
        return ContinueValue(
            value=None,
            env=ctx.env,
            store=None,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, GatherEffect):
        import os
        debug = os.environ.get("DOEFF_DEBUG")
        futures = effect.futures
        if not futures:
            return ContinueValue(
                value=[],
                env=ctx.env,
                store=None,
                k=ctx.delimited_k,
            )
        
        partial = effect._partial_results
        results: list[Any] = list(partial) if partial else [None] * len(futures)
        pending_indices: list[int] = []
        if debug:
            print(f"[GatherEffect] partial={partial}, results={results}, delimited_k_len={len(ctx.delimited_k)}, outer_k_len={len(ctx.outer_k)}, delimited_k_types={[type(f).__name__ for f in ctx.delimited_k[:10]]}")
        
        for i, future in enumerate(futures):
            if partial and partial[i] is not None:
                continue
            
            if not isinstance(future, Waitable):
                return ContinueError(
                    error=TypeError(f"Gather requires Waitable, got {type(future).__name__}"),
                    env=ctx.env,
                    store=None,
                    k=ctx.delimited_k,
                )
            
            handle_id = future._handle
            task_result = yield GetTaskResult(handle_id=handle_id)
            
            if task_result is None:
                return ContinueError(
                    error=ValueError(f"Invalid task handle: {handle_id}"),
                    env=ctx.env,
                    store=None,
                    k=ctx.delimited_k,
                )
            
            is_complete, is_cancelled, value, error = task_result
            if debug:
                print(f"[GatherEffect] future[{i}] is_complete={is_complete}, value={value}")
            
            if is_complete:
                if is_cancelled:
                    return ContinueError(
                        error=TaskCancelledError(),
                        env=ctx.env,
                        store=None,
                        k=ctx.delimited_k,
                    )
                if error is not None:
                    return ContinueError(
                        error=error,
                        env=ctx.env,
                        store=None,
                        k=ctx.delimited_k,
                    )
                results[i] = value
            else:
                pending_indices.append(i)
        
        if debug:
            print(f"[GatherEffect] pending_indices={pending_indices}, results={results}")
        if not pending_indices:
            if debug:
                print(f"[GatherEffect] Returning results={results}, k_len={len(ctx.delimited_k)}, k_types={[type(f).__name__ for f in ctx.delimited_k[:5]]}")
            return ContinueValue(
                value=results,
                env=ctx.env,
                store=None,
                k=ctx.delimited_k,
            )
        
        updated_effect = GatherEffect(
            futures=futures,
            _partial_results=tuple(results),
        )
        
        from doeff.cesk.frames import GatherWaiterFrame
        
        waiter_frame = GatherWaiterFrame(
            gather_effect=updated_effect,
            saved_env=ctx.env,
        )
        waiter_k = [waiter_frame] + list(ctx.delimited_k)
        
        current_task_id = yield GetCurrentTaskId()
        for i in pending_indices:
            handle_id = futures[i]._handle
            yield RegisterWaiter(
                handle_id=handle_id,
                waiter_task_id=current_task_id,
                waiter_k=waiter_k,
                waiter_store=dict(ctx.store),
            )
        
        next_task = yield QueuePop()
        if next_task is None:
            pending_io = ctx.store.get(PENDING_IO_KEY, {})
            if pending_io:
                from doeff.cesk.frames import SuspendOn
                return SuspendOn(
                    awaitable=None,
                    stored_k=waiter_k,
                    stored_env=ctx.env,
                    stored_store=ctx.store,
                )
            return ContinueError(
                error=RuntimeError("Deadlock: waiting for tasks but no other tasks to run"),
                env=ctx.env,
                store=None,
                k=ctx.delimited_k,
            )
        
        next_task_id, next_k, next_store, resume_value, resume_error, current_store = next_task
        task_store = dict(next_store) if next_store is not None else {}
        for key, val in current_store.items():
            if isinstance(key, str) and key.startswith("__scheduler_"):
                task_store[key] = val
        task_store[CURRENT_TASK_KEY] = next_task_id
        if resume_error is not None:
            return ContinueError(
                error=resume_error,
                env=ctx.env,
                store=task_store,
                k=next_k,
            )
        return ResumeK(k=next_k, value=resume_value, store=task_store)
    
    if isinstance(effect, RaceEffect):
        futures = effect.futures
        if not futures:
            return ContinueError(
                error=ValueError("Race requires at least one future"),
                env=ctx.env,
                store=ctx.store,
                k=ctx.delimited_k,
            )
        
        for future in futures:
            if not isinstance(future, Task):
                return ContinueError(
                    error=TypeError(f"Race requires Tasks, got {type(future).__name__}"),
                    env=ctx.env,
                    store=ctx.store,
                    k=ctx.delimited_k,
                )
            
            handle_id = future._handle
            task_result = yield GetTaskResult(handle_id=handle_id)
            
            if task_result is None:
                return ContinueError(
                    error=ValueError(f"Invalid task handle: {handle_id}"),
                    env=ctx.env,
                    store=ctx.store,
                    k=ctx.delimited_k,
                )
            
            is_complete, is_cancelled, value, error = task_result
            
            if is_complete:
                if is_cancelled:
                    return ContinueError(
                        error=TaskCancelledError(),
                        env=ctx.env,
                        store=ctx.store,
                        k=ctx.delimited_k,
                    )
                if error is not None:
                    return ContinueError(
                        error=error,
                        env=ctx.env,
                        store=ctx.store,
                        k=ctx.delimited_k,
                    )
                rest = tuple(f for f in futures if f is not future)
                race_result = RaceResult(first=future, value=value, rest=rest)
                return ContinueValue(
                    value=race_result,
                    env=ctx.env,
                    store=ctx.store,
                    k=ctx.delimited_k,
                )
        
        from doeff.cesk.frames import RaceWaiterFrame
        
        waiter_frame = RaceWaiterFrame(
            race_effect=effect,
            saved_env=ctx.env,
        )
        waiter_k = [waiter_frame] + list(ctx.delimited_k)
        
        current_task_id = yield GetCurrentTaskId()
        for future in futures:
            handle_id = future._handle
            yield RegisterWaiter(
                handle_id=handle_id,
                waiter_task_id=current_task_id,
                waiter_k=waiter_k,
                waiter_store=dict(ctx.store),
            )
        
        next_task = yield QueuePop()
        if next_task is None:
            pending_io = ctx.store.get(PENDING_IO_KEY, {})
            if pending_io:
                from doeff.cesk.frames import SuspendOn
                return SuspendOn(
                    awaitable=None,
                    stored_k=waiter_k,
                    stored_env=ctx.env,
                    stored_store=ctx.store,
                )
            return ContinueError(
                error=RuntimeError("Deadlock: racing but no other tasks to run"),
                env=ctx.env,
                store=ctx.store,
                k=ctx.delimited_k,
            )
        
        next_task_id, next_k, next_store, resume_value, resume_error, current_store = next_task
        task_store = dict(next_store) if next_store is not None else {}
        for key, val in current_store.items():
            if isinstance(key, str) and key.startswith("__scheduler_"):
                task_store[key] = val
        task_store[CURRENT_TASK_KEY] = next_task_id
        if resume_error is not None:
            return ContinueError(
                error=resume_error,
                env=ctx.env,
                store=task_store,
                k=next_k,
            )
        return ResumeK(k=next_k, value=resume_value, store=task_store)
    
    from doeff.effects.queue import SuspendForIOEffect
    from doeff.cesk.frames import SuspendOn
    
    if isinstance(effect, SuspendForIOEffect):
        import os
        debug = os.environ.get("DOEFF_DEBUG")
        current_task_id = yield GetCurrentTaskId()
        
        full_resume_k = list(ctx.delimited_k) + list(ctx.outer_k)
        
        new_store = dict(ctx.store)
        pending_io = dict(new_store.get(PENDING_IO_KEY, {}))
        pending_io[current_task_id] = {
            "awaitable": effect.awaitable,
            "k": full_resume_k,
            "store_snapshot": dict(ctx.store),
        }
        new_store[PENDING_IO_KEY] = pending_io
        
        queue = list(new_store.get(TASK_QUEUE_KEY, []))
        if debug:
            print(f"[SuspendForIOEffect] queue_len={len(queue)}, current_task={current_task_id}")
        if queue:
            item = queue.pop(0)
            new_store[TASK_QUEUE_KEY] = queue
            next_task_id = item["task_id"]
            next_k = item["k"]
            next_store_snapshot = item.get("store_snapshot")
            resume_value = item.get("resume_value")
            resume_error = item.get("resume_error")
            
            task_store = dict(next_store_snapshot) if next_store_snapshot else dict(new_store)
            task_store[CURRENT_TASK_KEY] = next_task_id
            task_store[PENDING_IO_KEY] = new_store[PENDING_IO_KEY]
            task_store[TASK_QUEUE_KEY] = new_store[TASK_QUEUE_KEY]
            task_store[TASK_REGISTRY_KEY] = new_store.get(TASK_REGISTRY_KEY, {})
            task_store[WAITERS_KEY] = new_store.get(WAITERS_KEY, {})
            
            if resume_error is not None:
                return ContinueError(
                    error=resume_error,
                    env=ctx.env,
                    store=task_store,
                    k=next_k,
                )
            return ResumeK(k=next_k, value=resume_value, store=task_store)
        
        return SuspendOn(
            awaitable=None,
            stored_k=ctx.delimited_k,
            stored_env=ctx.env,
            stored_store=new_store,
        )
    
    result = yield effect
    return ContinueValue(
        value=result,
        env=ctx.env,
        store=None,
        k=ctx.delimited_k,
    )


__all__ = [
    "scheduler_handler",
]
