"""Scheduler handler for cooperative multi-task scheduling.

Pure handler approach: all task scheduling happens through handlers.
No task tracking in outer runtime - just step until Done/Failed.
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
    from doeff.cesk.handlers.core_handler import core_handler
    
    wrapped = _make_spawn_wrapper(program, task_id, handle_id)
    
    return WithHandler(
        handler=cast(Handler, scheduler_handler),
        program=WithHandler(
            handler=cast(Handler, core_handler),
            program=wrapped,
        ),
    )


def _make_initial_k(program: Any) -> list[Any]:
    """Create initial continuation for a program."""
    gen = to_generator(program)
    return [ReturnFrame(gen, FrozenDict())]


@do  # type: ignore[reportArgumentType] - @do transforms generator to KleisliProgram
def scheduler_handler(effect: EffectBase, ctx: HandlerContext):
    """Handle scheduling effects using ResumeK for task switching."""
    
    if isinstance(effect, SpawnEffect):
        task_id = uuid4()
        env_snapshot = dict(ctx.env)
        store_snapshot = dict(ctx.store)
        
        handle_id, task_handle = yield CreateTaskHandle(
            task_id=task_id,
            env_snapshot=env_snapshot,
            store_snapshot=store_snapshot,
        )
        
        wrapped_program = _wrap_with_handlers_for_spawn(effect.program, task_id, handle_id)
        child_k = _make_initial_k(wrapped_program)
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
        
        next_task_id, next_k, next_store, resume_value, resume_error = next_task
        task_store = next_store if next_store is not None else ctx.store
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
            waiter_k=ctx.delimited_k,
            waiter_store=dict(ctx.store),
        )
        
        next_task = yield QueuePop()
        if next_task is None:
            return ContinueError(
                error=RuntimeError("Deadlock: waiting for task but no other tasks to run"),
                env=ctx.env,
                store=ctx.store,
                k=ctx.delimited_k,
            )
        
        next_task_id, next_k, next_store, resume_value, resume_error = next_task
        task_store = next_store if next_store is not None else ctx.store
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
        
        if not pending_indices:
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
            return ContinueError(
                error=RuntimeError("Deadlock: waiting for tasks but no other tasks to run"),
                env=ctx.env,
                store=None,
                k=ctx.delimited_k,
            )
        
        next_task_id, next_k, next_store, resume_value, resume_error = next_task
        task_store = next_store if next_store is not None else ctx.store
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
            return ContinueError(
                error=RuntimeError("Deadlock: racing but no other tasks to run"),
                env=ctx.env,
                store=ctx.store,
                k=ctx.delimited_k,
            )
        
        next_task_id, next_k, next_store, resume_value, resume_error = next_task
        task_store = next_store if next_store is not None else ctx.store
        if resume_error is not None:
            return ContinueError(
                error=resume_error,
                env=ctx.env,
                store=task_store,
                k=next_k,
            )
        return ResumeK(k=next_k, value=resume_value, store=task_store)
    
    from doeff.effects.future import FutureAwaitEffect
    from doeff.effects.time import DelayEffect
    
    if isinstance(effect, FutureAwaitEffect):
        import asyncio
        from collections.abc import Coroutine
        try:
            result = asyncio.run(cast(Coroutine[Any, Any, Any], effect.awaitable))
            return ContinueValue(
                value=result,
                env=ctx.env,
                store=None,
                k=ctx.delimited_k,
            )
        except Exception as e:
            return ContinueError(
                error=e,
                env=ctx.env,
                store=None,
                k=ctx.delimited_k,
            )
    
    if isinstance(effect, DelayEffect):
        import time
        time.sleep(effect.seconds)
        return ContinueValue(
            value=None,
            env=ctx.env,
            store=None,
            k=ctx.delimited_k,
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
