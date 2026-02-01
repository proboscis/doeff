"""Task scheduler handler for cooperative multi-task scheduling.

Pure handler approach: all task scheduling happens through handlers.
No task tracking in outer runtime - just step until Done/Failed.

## Architecture Notes

### P2: FutureAwaitEffect and DelayEffect Handling (ISSUE-CORE-467)

The task_scheduler_handler currently handles FutureAwaitEffect and DelayEffect directly
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

from doeff._types_internal import EffectBase
from doeff._vendor import FrozenDict
from doeff.cesk.frames import ReturnFrame
from doeff.cesk.handler_frame import Handler, HandlerContext, ResumeK, WithHandler
from doeff.cesk.state import CESKState
from doeff.cesk.handlers.scheduler_state_handler import (
    CURRENT_TASK_KEY,
    PENDING_IO_KEY,
    TASK_QUEUE_KEY,
    TASK_REGISTRY_KEY,
    WAITERS_KEY,
)
from doeff.cesk.helpers import to_generator
from doeff.cesk.result import multi_task_async_escape
from doeff.do import do
from doeff.effects.gather import GatherEffect
from doeff.effects.promise import (
    CompletePromiseEffect,
    CreatePromiseEffect,
    FailPromiseEffect,
)
from doeff.effects.race import RaceEffect, RaceResult
from doeff.effects.scheduler_internal import (
    _AsyncEscapeIntercepted,
    _SchedulerCancelTask,
    _SchedulerCreatePromise,
    _SchedulerCreateTaskHandle,
    _SchedulerDequeueTask,
    _SchedulerEnqueueTask,
    _SchedulerGetCurrentTaskId,
    _SchedulerGetTaskResult,
    _SchedulerIsTaskDone,
    _SchedulerRegisterWaiter,
    _SchedulerSuspendForIO,
    _SchedulerTaskComplete,
    _SchedulerTaskCompleted,
)
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
    """Wrap spawned program to yield _SchedulerTaskCompleted on completion."""

    @do
    def wrapper():
        try:
            result = yield program
            yield _SchedulerTaskCompleted(task_id=task_id, handle_id=handle_id, result=result)
        except Exception as e:
            yield _SchedulerTaskCompleted(task_id=task_id, handle_id=handle_id, error=e)

    return wrapper()


def _wrap_with_handlers_for_spawn(program: Any, task_id: Any, handle_id: Any) -> Any:
    """Wrap a spawned program with handlers and completion wrapper.

    Note: We do NOT wrap with scheduler_state_handler here. The scheduler_state_handler manages
    global scheduler state (queue, registry, waiters) that must be shared
    across all tasks. Effects from spawned tasks bubble up to the main
    scheduler_state_handler through the continuation stack.
    """
    from doeff.cesk.handlers.core_handler import core_handler
    from doeff.cesk.handlers.python_async_handler import python_async_handler

    wrapped = _make_spawn_wrapper(program, task_id, handle_id)

    return WithHandler(
        handler=cast(Handler, task_scheduler_handler),
        program=WithHandler(
            handler=cast(Handler, python_async_handler),
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


def _build_multi_task_escape_from_pending(
    pending_io: dict[Any, Any],
    stored_env: Any,
    stored_store: dict[str, Any],
) -> Any:
    """Build PythonAsyncSyntaxEscape for multi-task case using stored escapes."""
    from doeff.cesk.result import PythonAsyncSyntaxEscape
    from doeff.cesk.state import CESKState, Error

    awaitables_dict = {task_id: info["awaitable"] for task_id, info in pending_io.items()}

    def resume_multi(value: Any, new_store: dict[str, Any]) -> CESKState:
        task_id, result = value
        task_info = pending_io.get(task_id)
        if task_info is None:
            raise RuntimeError(f"Task {task_id} not found in pending_io")

        escape = task_info["escape"]
        task_store_snapshot = task_info.get("store_snapshot", {})

        new_pending = dict(pending_io)
        del new_pending[task_id]

        merged_store = dict(task_store_snapshot)
        for key, val in stored_store.items():
            if isinstance(key, str) and key.startswith("__scheduler_"):
                merged_store[key] = val
        merged_store[PENDING_IO_KEY] = new_pending
        merged_store[CURRENT_TASK_KEY] = task_id

        return escape.resume(result, merged_store)

    def resume_error_multi(error_info: Any) -> CESKState:
        task_id, error = error_info
        task_info = pending_io.get(task_id)
        if task_info is None:
            return CESKState(
                C=Error(error),
                E=stored_env,
                S=stored_store,
                K=[],
            )

        escape = task_info["escape"]
        task_store_snapshot = task_info.get("store_snapshot", {})

        new_pending = dict(pending_io)
        del new_pending[task_id]

        merged_store = dict(task_store_snapshot)
        for key, val in stored_store.items():
            if isinstance(key, str) and key.startswith("__scheduler_"):
                merged_store[key] = val
        merged_store[PENDING_IO_KEY] = new_pending
        merged_store[CURRENT_TASK_KEY] = task_id

        # CRITICAL: Do NOT call escape.resume_error(error) here!
        # The escape's resume_error uses its stored_store snapshot from escape creation,
        # which has STALE scheduler state (e.g., tasks in queue that are now in pending_io).
        # Instead, construct CESKState directly with the correct merged_store.
        stored_k = escape._stored_k if escape._stored_k is not None else []
        return CESKState(
            C=Error(error),
            E=stored_env,
            S=merged_store,
            K=list(stored_k),
        )

    return PythonAsyncSyntaxEscape(
        resume=resume_multi,
        resume_error=resume_error_multi,
        awaitables=awaitables_dict,
        store=stored_store,
    )


@do  # type: ignore[reportArgumentType] - @do transforms generator to KleisliProgram
def task_scheduler_handler(effect: EffectBase, ctx: HandlerContext):
    """Handle scheduling effects using ResumeK for task switching."""

    if isinstance(effect, _AsyncEscapeIntercepted):
        escape = effect.escape
        current_task_id = ctx.store.get(CURRENT_TASK_KEY)

        new_store = dict(ctx.store)
        pending_io = dict(new_store.get(PENDING_IO_KEY, {}))

        stored_k = escape._stored_k if escape._stored_k is not None else []
        stored_store = escape._stored_store if escape._stored_store is not None else dict(ctx.store)

        pending_io[current_task_id] = {
            "awaitable": escape.awaitable,
            "escape": escape,
            "k": list(stored_k),
            "store_snapshot": dict(stored_store),
        }
        new_store[PENDING_IO_KEY] = pending_io

        queue = list(new_store.get(TASK_QUEUE_KEY, []))

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
                return CESKState.with_error(resume_error, ctx.env, task_store, next_k)
            return ResumeK(k=next_k, value=resume_value, store=task_store)

        if len(pending_io) > 1:
            return _build_multi_task_escape_from_pending(
                pending_io=pending_io,
                stored_env=ctx.env,
                stored_store=new_store,
            )

        from doeff.cesk.result import PythonAsyncSyntaxEscape

        return PythonAsyncSyntaxEscape(
            resume=escape.resume,
            resume_error=escape.resume_error,
            awaitable=escape.awaitable,
            awaitables=escape.awaitables,
            store=escape.store,
            _propagating=True,
            _is_final=True,
            _stored_k=escape._stored_k,
            _stored_env=escape._stored_env,
            _stored_store=escape._stored_store,
        )

    if isinstance(effect, SpawnEffect):
        task_id = uuid4()
        env_snapshot = dict(ctx.env)
        store_snapshot = dict(ctx.store)
        store_snapshot[CURRENT_TASK_KEY] = task_id

        handle_id, task_handle = yield _SchedulerCreateTaskHandle(
            task_id=task_id,
            env_snapshot=env_snapshot,
            store_snapshot=store_snapshot,
        )

        wrapped_program = _wrap_with_handlers_for_spawn(effect.program, task_id, handle_id)
        child_k = _make_initial_k(wrapped_program, env_snapshot)
        yield _SchedulerEnqueueTask(task_id=task_id, k=child_k, store_snapshot=store_snapshot)

        # Return plain value - HandlerResultFrame constructs CESKState with current store
        return task_handle

    if isinstance(effect, _SchedulerTaskCompleted):
        yield _SchedulerTaskComplete(
            handle_id=effect.handle_id,
            task_id=effect.task_id,
            result=effect.result,
            error=effect.error,
        )

        next_task = yield _SchedulerDequeueTask()
        if next_task is None:
            pending_io = ctx.store.get(PENDING_IO_KEY, {})
            if pending_io:
                has_escape = any("escape" in info for info in pending_io.values())
                if has_escape:
                    return _build_multi_task_escape_from_pending(
                        pending_io=pending_io,
                        stored_env=ctx.env,
                        stored_store=ctx.store,
                    )
                return multi_task_async_escape(
                    stored_k=ctx.delimited_k,
                    stored_env=ctx.env,
                    stored_store=ctx.store,
                )
            if effect.error is not None:
                return CESKState.with_error(effect.error, ctx.env, ctx.store, [])
            return CESKState.with_value(effect.result, ctx.env, ctx.store, [])

        next_task_id, next_k, next_store, resume_value, resume_error, current_store = next_task
        task_store = dict(next_store) if next_store is not None else {}
        for key, val in current_store.items():
            if isinstance(key, str) and key.startswith("__scheduler_"):
                task_store[key] = val
        task_store[CURRENT_TASK_KEY] = next_task_id
        if resume_error is not None:
            return ResumeK(k=next_k, error=resume_error, store=task_store)
        return ResumeK(k=next_k, value=resume_value, store=task_store)

    if isinstance(effect, WaitEffect):
        waitable = effect.future
        if not isinstance(waitable, Waitable):
            return CESKState.with_error(
                TypeError(f"Wait requires Waitable, got {type(waitable).__name__}"),
                ctx.env, ctx.store, ctx.k,
            )

        handle_id = waitable._handle
        result = yield _SchedulerGetTaskResult(handle_id=handle_id)

        if result is None:
            return CESKState.with_error(
                ValueError(f"Invalid task handle: {handle_id}"),
                ctx.env, ctx.store, ctx.k,
            )

        is_complete, is_cancelled, task_result, task_error = result

        if is_complete:
            if is_cancelled:
                return CESKState.with_error(TaskCancelledError(), ctx.env, ctx.store, ctx.k)
            if task_error is not None:
                return CESKState.with_error(task_error, ctx.env, ctx.store, ctx.k)
            return CESKState.with_value(task_result, ctx.env, ctx.store, ctx.k)

        current_task_id = yield _SchedulerGetCurrentTaskId()
        yield _SchedulerRegisterWaiter(
            handle_id=handle_id,
            waiter_task_id=current_task_id,
            waiter_k=list(ctx.delimited_k),
            waiter_store=dict(ctx.store),
        )

        next_task = yield _SchedulerDequeueTask()
        if next_task is None:
            pending_io = ctx.store.get(PENDING_IO_KEY, {})
            if pending_io:
                has_escape = any("escape" in info for info in pending_io.values())
                if has_escape:
                    return _build_multi_task_escape_from_pending(
                        pending_io=pending_io,
                        stored_env=ctx.env,
                        stored_store=ctx.store,
                    )
                return multi_task_async_escape(
                    stored_k=list(ctx.delimited_k),
                    stored_env=ctx.env,
                    stored_store=ctx.store,
                )
            return CESKState.with_error(
                RuntimeError("Deadlock: waiting for task but no other tasks to run"),
                ctx.env, ctx.store, ctx.k,
            )

        next_task_id, next_k, next_store, resume_value, resume_error, current_store = next_task
        task_store = dict(next_store) if next_store is not None else {}
        for key, val in current_store.items():
            if isinstance(key, str) and key.startswith("__scheduler_"):
                task_store[key] = val
        task_store[CURRENT_TASK_KEY] = next_task_id
        if resume_error is not None:
            return ResumeK(k=next_k, error=resume_error, store=task_store)
        return ResumeK(k=next_k, value=resume_value, store=task_store)

    if isinstance(effect, TaskCancelEffect):
        handle_id = effect.task._handle
        was_cancelled = yield _SchedulerCancelTask(handle_id=handle_id)
        # Return plain value - HandlerResultFrame constructs CESKState with current store
        return was_cancelled

    if isinstance(effect, TaskIsDoneEffect):
        handle_id = effect.task._handle
        is_done = yield _SchedulerIsTaskDone(handle_id=handle_id)
        # Return plain value - HandlerResultFrame constructs CESKState with current store
        return is_done

    if isinstance(effect, CreatePromiseEffect):
        handle_id, promise = yield _SchedulerCreatePromise()
        # Return plain value - HandlerResultFrame constructs CESKState with current store
        return promise

    if isinstance(effect, CompletePromiseEffect):
        handle_id = effect.promise.future._handle
        result = yield _SchedulerGetTaskResult(handle_id=handle_id)

        if result is None:
            return CESKState.with_error(
                ValueError(f"Invalid promise handle: {handle_id}"),
                ctx.env, ctx.store, ctx.k,
            )

        is_complete, _, _, _ = result
        if is_complete:
            return CESKState.with_error(
                RuntimeError("Promise already completed"),
                ctx.env, ctx.store, ctx.k,
            )

        yield _SchedulerTaskComplete(
            handle_id=handle_id,
            task_id=None,
            result=effect.value,
            error=None,
        )

        # Return plain value - HandlerResultFrame constructs CESKState with current store
        return None

    if isinstance(effect, FailPromiseEffect):
        handle_id = effect.promise.future._handle
        result = yield _SchedulerGetTaskResult(handle_id=handle_id)

        if result is None:
            return CESKState.with_error(
                ValueError(f"Invalid promise handle: {handle_id}"),
                ctx.env, ctx.store, ctx.k,
            )

        is_complete, _, _, _ = result
        if is_complete:
            return CESKState.with_error(
                RuntimeError("Promise already completed"),
                ctx.env, ctx.store, ctx.k,
            )

        yield _SchedulerTaskComplete(
            handle_id=handle_id,
            task_id=None,
            result=None,
            error=effect.error,
        )

        # Return plain value - HandlerResultFrame constructs CESKState with current store
        return None

    if isinstance(effect, GatherEffect):
        import os

        debug = os.environ.get("DOEFF_DEBUG", "").lower() in ("1", "true", "yes")
        futures = effect.futures
        if not futures:
            return CESKState.with_value([], ctx.env, ctx.store, ctx.k)

        partial = effect._partial_results
        results: list[Any] = list(partial) if partial else [None] * len(futures)
        pending_indices: list[int] = []
        if debug:
            print(
                f"[GatherEffect] partial={partial}, results={results}, delimited_k_len={len(ctx.delimited_k)}, outer_k_len={len(ctx.outer_k)}, delimited_k_types={[type(f).__name__ for f in ctx.delimited_k[:10]]}"
            )

        for i, future in enumerate(futures):
            if partial and partial[i] is not None:
                continue

            if not isinstance(future, Waitable):
                return CESKState.with_error(
                    TypeError(f"Gather requires Waitable, got {type(future).__name__}"),
                    ctx.env, ctx.store, ctx.k,
                )

            handle_id = future._handle
            task_result = yield _SchedulerGetTaskResult(handle_id=handle_id)

            if task_result is None:
                return CESKState.with_error(
                    ValueError(f"Invalid task handle: {handle_id}"),
                    ctx.env, ctx.store, ctx.k,
                )

            is_complete, is_cancelled, value, error = task_result
            if debug:
                print(f"[GatherEffect] future[{i}] is_complete={is_complete}, value={value}")

            if is_complete:
                if is_cancelled:
                    return CESKState.with_error(TaskCancelledError(), ctx.env, ctx.store, ctx.k)
                if error is not None:
                    return CESKState.with_error(error, ctx.env, ctx.store, ctx.k)
                results[i] = value
            else:
                pending_indices.append(i)

        if debug:
            print(f"[GatherEffect] pending_indices={pending_indices}, results={results}")
        if not pending_indices:
            if debug:
                print(
                    f"[GatherEffect] Returning results={results}, k_len={len(ctx.delimited_k)}, k_types={[type(f).__name__ for f in ctx.delimited_k[:5]]}"
                )
            return CESKState.with_value(results, ctx.env, ctx.store, ctx.k)

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

        current_task_id = yield _SchedulerGetCurrentTaskId()
        for i in pending_indices:
            handle_id = futures[i]._handle
            yield _SchedulerRegisterWaiter(
                handle_id=handle_id,
                waiter_task_id=current_task_id,
                waiter_k=waiter_k,
                waiter_store=dict(ctx.store),
            )

        next_task = yield _SchedulerDequeueTask()
        if next_task is None:
            pending_io = ctx.store.get(PENDING_IO_KEY, {})
            if pending_io:
                has_escape = any("escape" in info for info in pending_io.values())
                if has_escape:
                    return _build_multi_task_escape_from_pending(
                        pending_io=pending_io,
                        stored_env=ctx.env,
                        stored_store=ctx.store,
                    )
                return multi_task_async_escape(
                    stored_k=waiter_k,
                    stored_env=ctx.env,
                    stored_store=ctx.store,
                )
            return CESKState.with_error(
                RuntimeError("Deadlock: waiting for tasks but no other tasks to run"),
                ctx.env, ctx.store, ctx.k,
            )

        next_task_id, next_k, next_store, resume_value, resume_error, current_store = next_task
        task_store = dict(next_store) if next_store is not None else {}
        for key, val in current_store.items():
            if isinstance(key, str) and key.startswith("__scheduler_"):
                task_store[key] = val
        task_store[CURRENT_TASK_KEY] = next_task_id
        if resume_error is not None:
            return ResumeK(k=next_k, error=resume_error, store=task_store)
        return ResumeK(k=next_k, value=resume_value, store=task_store)

    if isinstance(effect, RaceEffect):
        futures = effect.futures
        if not futures:
            return CESKState.with_error(
                ValueError("Race requires at least one future"),
                ctx.env, ctx.store, ctx.k,
            )

        for future in futures:
            if not isinstance(future, Task):
                return CESKState.with_error(
                    TypeError(f"Race requires Tasks, got {type(future).__name__}"),
                    ctx.env, ctx.store, ctx.k,
                )

            handle_id = future._handle
            task_result = yield _SchedulerGetTaskResult(handle_id=handle_id)

            if task_result is None:
                return CESKState.with_error(
                    ValueError(f"Invalid task handle: {handle_id}"),
                    ctx.env, ctx.store, ctx.k,
                )

            is_complete, is_cancelled, value, error = task_result

            if is_complete:
                if is_cancelled:
                    return CESKState.with_error(TaskCancelledError(), ctx.env, ctx.store, ctx.k)
                if error is not None:
                    return CESKState.with_error(error, ctx.env, ctx.store, ctx.k)
                rest = tuple(f for f in futures if f is not future)
                race_result = RaceResult(first=future, value=value, rest=rest)
                return CESKState.with_value(race_result, ctx.env, ctx.store, ctx.k)

        from doeff.cesk.frames import RaceWaiterFrame

        waiter_frame = RaceWaiterFrame(
            race_effect=effect,
            saved_env=ctx.env,
        )
        waiter_k = [waiter_frame] + list(ctx.delimited_k)

        current_task_id = yield _SchedulerGetCurrentTaskId()
        for future in futures:
            handle_id = future._handle
            yield _SchedulerRegisterWaiter(
                handle_id=handle_id,
                waiter_task_id=current_task_id,
                waiter_k=waiter_k,
                waiter_store=dict(ctx.store),
            )

        next_task = yield _SchedulerDequeueTask()
        if next_task is None:
            pending_io = ctx.store.get(PENDING_IO_KEY, {})
            if pending_io:
                has_escape = any("escape" in info for info in pending_io.values())
                if has_escape:
                    return _build_multi_task_escape_from_pending(
                        pending_io=pending_io,
                        stored_env=ctx.env,
                        stored_store=ctx.store,
                    )
                return multi_task_async_escape(
                    stored_k=waiter_k,
                    stored_env=ctx.env,
                    stored_store=ctx.store,
                )
            return CESKState.with_error(
                RuntimeError("Deadlock: racing but no other tasks to run"),
                ctx.env, ctx.store, ctx.k,
            )

        next_task_id, next_k, next_store, resume_value, resume_error, current_store = next_task
        task_store = dict(next_store) if next_store is not None else {}
        for key, val in current_store.items():
            if isinstance(key, str) and key.startswith("__scheduler_"):
                task_store[key] = val
        task_store[CURRENT_TASK_KEY] = next_task_id
        if resume_error is not None:
            return ResumeK(k=next_k, error=resume_error, store=task_store)
        return ResumeK(k=next_k, value=resume_value, store=task_store)

    if isinstance(effect, _SchedulerSuspendForIO):
        import os

        debug = os.environ.get("DOEFF_DEBUG", "").lower() in ("1", "true", "yes")
        current_task_id = ctx.store.get(CURRENT_TASK_KEY)

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
            print(
                f"[_SchedulerSuspendForIO] queue_len={len(queue)}, current_task={current_task_id}"
            )
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
                return CESKState.with_error(resume_error, ctx.env, task_store, next_k)
            return ResumeK(k=next_k, value=resume_value, store=task_store)

        has_escape = any("escape" in info for info in new_store.get(PENDING_IO_KEY, {}).values())
        if has_escape:
            return _build_multi_task_escape_from_pending(
                pending_io=new_store.get(PENDING_IO_KEY, {}),
                stored_env=ctx.env,
                stored_store=new_store,
            )
        return multi_task_async_escape(
            stored_k=ctx.delimited_k,
            stored_env=ctx.env,
            stored_store=new_store,
        )

    # Forward unhandled effects to outer handler
    result = yield effect
    # Return plain value - HandlerResultFrame constructs CESKState with current store
    return result


# Backwards compatibility alias (deprecated)
scheduler_handler = task_scheduler_handler


__all__ = [
    "scheduler_handler",
    "task_scheduler_handler",
]
