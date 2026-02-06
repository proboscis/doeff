"""Task scheduler handler for cooperative multi-task scheduling.

Pure handler approach: all task scheduling happens through handlers.
No task tracking in outer runtime - just step until Done/Failed.

## Architecture Notes

### P2: PythonAsyncioAwaitEffect and DelayEffect Handling (ISSUE-CORE-467)

The task_scheduler_handler currently handles PythonAsyncioAwaitEffect and DelayEffect
directly using blocking I/O operations (asyncio.run and time.sleep). This is intentional
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
- PythonAsyncioAwaitEffect, DelayEffect (line ~529-530)

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
from doeff.cesk.handler_frame import Handler, HandlerContext, HandlerFrame, ResumeK, WithHandler
from doeff.cesk.state import CESKState, Value
from doeff.cesk.handlers.scheduler_state_handler import (
    CURRENT_TASK_KEY,
    EXTERNAL_PROMISE_REGISTRY_KEY,
    TASK_QUEUE_KEY,
    TASK_REGISTRY_KEY,
    WAITERS_KEY,
)
from doeff.cesk.helpers import to_generator
from doeff.do import do
from doeff.effects.gather import GatherEffect
from doeff.effects.promise import (
    CompletePromiseEffect,
    CreatePromiseEffect,
    FailPromiseEffect,
)
from doeff.effects.race import RaceEffect, RaceResult
from doeff.effects.scheduler_internal import (
    WaitForExternalCompletion,
    _SchedulerCancelTask,
    _SchedulerCreatePromise,
    _SchedulerCreateTaskHandle,
    _SchedulerDequeueTask,
    _SchedulerEnqueueTask,
    _SchedulerGetCurrentTaskId,
    _SchedulerGetTaskResult,
    _SchedulerRegisterWaiter,
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


def _wrap_with_handlers_for_spawn(
    program: Any, task_id: Any, handle_id: Any, store: dict[str, Any]
) -> Any:
    """Wrap a spawned program with completion wrapper only.

    Spawned tasks inherit parent's handlers via k_rest when ResumeK is processed.
    We only add the completion wrapper to yield _SchedulerTaskCompleted.

    NO handler wrapping is done here - child effects bubble up to parent's handlers.
    """
    return _make_spawn_wrapper(program, task_id, handle_id)


def _make_initial_k(program: Any, env: Any = None) -> list[Any]:
    """Create initial continuation for a program."""
    gen = to_generator(program)
    saved_env = FrozenDict(env) if env else FrozenDict()
    return [ReturnFrame(gen, saved_env)]


def _extract_handler_frames(k: list[Any]) -> list[Any]:
    """Extract HandlerFrame elements from a continuation.

    This is a simple list filter - used as fallback when ctx.inherited_handlers
    is not available (e.g., in GatherEffect inline spawn).
    """
    return [frame for frame in k if isinstance(frame, HandlerFrame)]


@do  # type: ignore[reportArgumentType] - @do transforms generator to KleisliProgram
def task_scheduler_handler(effect: EffectBase, ctx: HandlerContext):
    """Handle scheduling effects using ResumeK for task switching.

    Note: This handler is transparent to PythonAsyncSyntaxEscape.
    Async escapes pass through unchanged. Task coordination for async I/O
    is handled via ExternalPromise + Wait, not via escape interception.
    See SPEC-CESK-005-simplify-async-escape.md.
    """

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

        wrapped_program = _wrap_with_handlers_for_spawn(
            effect.program, task_id, handle_id, ctx.store
        )
        base_k = _make_initial_k(wrapped_program, env_snapshot)
        # Inherit parent's handler frames so child effects go through same handlers
        # ctx.inherited_handlers is a simple list copy from the original K
        child_k = base_k + list(ctx.inherited_handlers)
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

        dequeue_result = yield _SchedulerDequeueTask()
        # dequeue_result is (None, current_store) when empty, or full tuple when task available
        if dequeue_result[0] is None:
            current_store = dequeue_result[1]
            external_registry = current_store.get(EXTERNAL_PROMISE_REGISTRY_KEY, {})
            waiters = current_store.get(WAITERS_KEY, {})

            # Other tasks may be waiting on external I/O - don't terminate early
            if external_registry and waiters:
                from doeff.cesk.handlers.scheduler_state_handler import (
                    EXTERNAL_COMPLETION_QUEUE_KEY,
                )

                completion_queue = current_store.get(EXTERNAL_COMPLETION_QUEUE_KEY)
                if completion_queue is not None:
                    promise_id, value, error = yield WaitForExternalCompletion(
                        queue=completion_queue
                    )

                    completed_handle_id = external_registry.get(promise_id)
                    if completed_handle_id is not None:
                        yield _SchedulerTaskComplete(
                            handle_id=completed_handle_id,
                            task_id=None,
                            result=value,
                            error=error,
                        )

                    dequeue_result = yield _SchedulerDequeueTask()
                    if dequeue_result[0] is not None:
                        (
                            next_task_id,
                            next_k,
                            next_store,
                            resume_value,
                            resume_error,
                            sched_store,
                        ) = dequeue_result
                        task_store = dict(next_store) if next_store is not None else {}
                        for key, val in sched_store.items():
                            if isinstance(key, str) and key.startswith("__scheduler_"):
                                task_store[key] = val
                        task_store[CURRENT_TASK_KEY] = next_task_id
                        if resume_error is not None:
                            return ResumeK(k=next_k, error=resume_error, store=task_store)
                        return ResumeK(k=next_k, value=resume_value, store=task_store)

            if effect.error is not None:
                return CESKState.with_error(effect.error, ctx.env, current_store, [])
            return CESKState.with_value(effect.result, ctx.env, current_store, [])

        next_task_id, next_k, next_store, resume_value, resume_error, current_store = dequeue_result
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
                ctx.env,
                ctx.store,
                ctx.k,
            )

        handle_id = waitable._handle
        result = yield _SchedulerGetTaskResult(handle_id=handle_id)

        if result is None:
            return CESKState.with_error(
                ValueError(f"Invalid task handle: {handle_id}"),
                ctx.env,
                ctx.store,
                ctx.k,
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
            waiter_k=list(ctx.k),  # Full continuation including handlers
            waiter_store=dict(ctx.store),
        )

        dequeue_result = yield _SchedulerDequeueTask()
        # dequeue_result is (None, current_store) when empty, or full tuple when task available
        if dequeue_result[0] is None:
            # Queue is empty - use current_store from dequeue result (has latest scheduler state)
            current_store = dequeue_result[1]

            # IMPORTANT: Re-check if task is complete. The completion might have been processed
            # by process_external_completions() between our _SchedulerGetTaskResult call and now.
            # This handles the race condition where the completion arrives mid-handler-execution.
            recheck_result = yield _SchedulerGetTaskResult(handle_id=handle_id)
            if recheck_result is not None:
                is_complete_now, is_cancelled_now, task_result_now, task_error_now = recheck_result
                if is_complete_now:
                    if is_cancelled_now:
                        return CESKState.with_error(
                            TaskCancelledError(), ctx.env, current_store, ctx.k
                        )
                    if task_error_now is not None:
                        return CESKState.with_error(task_error_now, ctx.env, current_store, ctx.k)
                    return CESKState.with_value(task_result_now, ctx.env, current_store, ctx.k)

            external_registry = current_store.get(EXTERNAL_PROMISE_REGISTRY_KEY, {})
            waiters = current_store.get(WAITERS_KEY, {})

            # Wait for ANY pending external I/O - it may transitively unblock our target
            if external_registry and waiters:
                from doeff.cesk.handlers.scheduler_state_handler import (
                    EXTERNAL_COMPLETION_QUEUE_KEY,
                )

                completion_queue = current_store.get(EXTERNAL_COMPLETION_QUEUE_KEY)
                if completion_queue is None:
                    return CESKState.with_error(
                        RuntimeError("No completion queue for external promise"),
                        ctx.env,
                        current_store,
                        ctx.k,
                    )

                promise_id, value, error = yield WaitForExternalCompletion(queue=completion_queue)

                completed_handle_id = external_registry.get(promise_id)
                if completed_handle_id is not None:
                    yield _SchedulerTaskComplete(
                        handle_id=completed_handle_id,
                        task_id=None,
                        result=value,
                        error=error,
                    )

                dequeue_result = yield _SchedulerDequeueTask()
                if dequeue_result[0] is not None:
                    next_task_id, next_k, next_store, resume_value, resume_error, sched_store = (
                        dequeue_result
                    )
                    task_store = dict(next_store) if next_store is not None else {}
                    for key, val in sched_store.items():
                        if isinstance(key, str) and key.startswith("__scheduler_"):
                            task_store[key] = val
                    task_store[CURRENT_TASK_KEY] = next_task_id
                    if resume_error is not None:
                        return ResumeK(k=next_k, error=resume_error, store=task_store)
                    return ResumeK(k=next_k, value=resume_value, store=task_store)

                return CESKState.with_error(
                    RuntimeError("Deadlock: external completion processed but no tasks to run"),
                    ctx.env,
                    current_store,
                    ctx.k,
                )
            return CESKState.with_error(
                RuntimeError("Deadlock: waiting for task but no other tasks to run"),
                ctx.env,
                current_store,
                ctx.k,
            )

        next_task_id, next_k, next_store, resume_value, resume_error, current_store = dequeue_result
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
        result = yield _SchedulerGetTaskResult(handle_id=handle_id)
        if result is None:
            return True
        is_complete, _, _, _ = result
        return is_complete

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
                ctx.env,
                ctx.store,
                ctx.k,
            )

        is_complete, _, _, _ = result
        if is_complete:
            return CESKState.with_error(
                RuntimeError("Promise already completed"),
                ctx.env,
                ctx.store,
                ctx.k,
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
                ctx.env,
                ctx.store,
                ctx.k,
            )

        is_complete, _, _, _ = result
        if is_complete:
            return CESKState.with_error(
                RuntimeError("Promise already completed"),
                ctx.env,
                ctx.store,
                ctx.k,
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

        from doeff.program import ProgramBase

        debug = os.environ.get("DOEFF_DEBUG", "").lower() in ("1", "true", "yes")
        items = effect.items
        if not items:
            return CESKState.with_value([], ctx.env, ctx.store, ctx.k)

        # Convert Programs to Tasks by spawning them (inline spawn logic)
        waitables: list[Any] = []
        for item in items:
            # SpawnEffect: extract program and spawn it
            # (SpawnEffect inherits from EffectBase which inherits from ProgramBase,
            #  but we want to spawn its inner program, not the SpawnEffect itself)
            if isinstance(item, SpawnEffect):
                item = item.program  # Extract inner program

            if isinstance(item, (ProgramBase, EffectBase)) and not isinstance(item, SpawnEffect):
                # Inline spawn logic - cannot yield SpawnEffect because it would
                # go to outer handler (scheduler_state_handler) which doesn't handle it
                task_id = uuid4()
                env_snapshot = dict(ctx.env)
                store_snapshot = dict(ctx.store)
                store_snapshot[CURRENT_TASK_KEY] = task_id

                handle_id, task_handle = yield _SchedulerCreateTaskHandle(
                    task_id=task_id,
                    env_snapshot=env_snapshot,
                    store_snapshot=store_snapshot,
                )

                wrapped_program = _wrap_with_handlers_for_spawn(item, task_id, handle_id, ctx.store)
                base_k = _make_initial_k(wrapped_program, env_snapshot)
                # Inherit parent's handler frames (same as SpawnEffect)
                child_k = base_k + list(ctx.inherited_handlers)
                yield _SchedulerEnqueueTask(
                    task_id=task_id, k=child_k, store_snapshot=store_snapshot
                )

                waitables.append(task_handle)
            elif isinstance(item, Waitable):
                waitables.append(item)
            else:
                return CESKState.with_error(
                    TypeError(f"Gather requires Waitable or Program, got {type(item).__name__}"),
                    ctx.env,
                    ctx.store,
                    ctx.k,
                )

        partial = effect._partial_results
        results: list[Any] = list(partial) if partial else [None] * len(waitables)
        pending_indices: list[int] = []
        if debug:
            print(
                f"[GatherEffect] partial={partial}, results={results}, delimited_k_len={len(ctx.delimited_k)}, outer_k_len={len(ctx.outer_k)}, delimited_k_types={[type(f).__name__ for f in ctx.delimited_k[:10]]}"
            )

        for i, future in enumerate(waitables):
            if partial and partial[i] is not None:
                continue

            handle_id = future._handle
            task_result = yield _SchedulerGetTaskResult(handle_id=handle_id)

            if task_result is None:
                return CESKState.with_error(
                    ValueError(f"Invalid task handle: {handle_id}"),
                    ctx.env,
                    ctx.store,
                    ctx.k,
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

        # Create updated effect with waitables (all now Tasks/Futures)
        updated_effect = GatherEffect(
            items=tuple(waitables),
            _partial_results=tuple(results),
        )

        from doeff.cesk.frames import GatherWaiterFrame

        waiter_frame = GatherWaiterFrame(
            gather_effect=updated_effect,
            saved_env=ctx.env,
        )
        # Full continuation: waiter_frame + delimited_k + outer_k (includes handlers)
        waiter_k = [waiter_frame] + list(ctx.delimited_k) + list(ctx.outer_k)

        current_task_id = yield _SchedulerGetCurrentTaskId()
        for i in pending_indices:
            handle_id = waitables[i]._handle
            yield _SchedulerRegisterWaiter(
                handle_id=handle_id,
                waiter_task_id=current_task_id,
                waiter_k=waiter_k,
                waiter_store=dict(ctx.store),
            )

        dequeue_result = yield _SchedulerDequeueTask()
        # dequeue_result is (None, current_store) when empty, or full tuple when task available
        if dequeue_result[0] is None:
            # Queue is empty - check if waiting for external promises
            current_store = dequeue_result[1]
            external_registry = current_store.get(EXTERNAL_PROMISE_REGISTRY_KEY, {})
            if debug:
                print(
                    f"[GatherEffect] Queue empty, external_registry keys={list(external_registry.keys())}, current_store scheduler keys={[k for k in current_store.keys() if isinstance(k, str) and k.startswith('__scheduler')]}"
                )
            if external_registry:
                # Waiting for external promise - yield to external wait handler
                # Per SPEC-CESK-004: scheduler yields WaitForExternalCompletion
                from doeff.cesk.handlers.scheduler_state_handler import (
                    EXTERNAL_COMPLETION_QUEUE_KEY,
                )

                completion_queue = current_store.get(EXTERNAL_COMPLETION_QUEUE_KEY)
                if completion_queue is None:
                    return CESKState.with_error(
                        RuntimeError("No completion queue for external promise"),
                        ctx.env,
                        current_store,
                        ctx.k,
                    )

                # Yield to external wait handler (sync or async)
                promise_id, value, error = yield WaitForExternalCompletion(queue=completion_queue)

                # Look up handle_id for this promise and mark complete
                completed_handle_id = external_registry.get(promise_id)
                if completed_handle_id is not None:
                    yield _SchedulerTaskComplete(
                        handle_id=completed_handle_id,
                        task_id=None,
                        result=value,
                        error=error,
                    )

                # Now waiter should be in queue - dequeue and resume
                dequeue_result = yield _SchedulerDequeueTask()
                if dequeue_result[0] is not None:
                    next_task_id, next_k, next_store, resume_value, resume_error, sched_store = (
                        dequeue_result
                    )
                    task_store = dict(next_store) if next_store is not None else {}
                    for key, val in sched_store.items():
                        if isinstance(key, str) and key.startswith("__scheduler_"):
                            task_store[key] = val
                    task_store[CURRENT_TASK_KEY] = next_task_id
                    if resume_error is not None:
                        return ResumeK(k=next_k, error=resume_error, store=task_store)
                    return ResumeK(k=next_k, value=resume_value, store=task_store)

                # Still no tasks? Shouldn't happen but handle gracefully
                return CESKState.with_error(
                    RuntimeError("Deadlock: external completion processed but no tasks to run"),
                    ctx.env,
                    current_store,
                    ctx.k,
                )
            return CESKState.with_error(
                RuntimeError("Deadlock: waiting for tasks but no other tasks to run"),
                ctx.env,
                current_store,
                ctx.k,
            )

        next_task_id, next_k, next_store, resume_value, resume_error, current_store = dequeue_result
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
                ctx.env,
                ctx.store,
                ctx.k,
            )

        for future in futures:
            if not isinstance(future, Task):
                return CESKState.with_error(
                    TypeError(f"Race requires Tasks, got {type(future).__name__}"),
                    ctx.env,
                    ctx.store,
                    ctx.k,
                )

            handle_id = future._handle
            task_result = yield _SchedulerGetTaskResult(handle_id=handle_id)

            if task_result is None:
                return CESKState.with_error(
                    ValueError(f"Invalid task handle: {handle_id}"),
                    ctx.env,
                    ctx.store,
                    ctx.k,
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
        # Full continuation: waiter_frame + delimited_k + outer_k (includes handlers)
        waiter_k = [waiter_frame] + list(ctx.delimited_k) + list(ctx.outer_k)

        current_task_id = yield _SchedulerGetCurrentTaskId()
        for future in futures:
            handle_id = future._handle
            yield _SchedulerRegisterWaiter(
                handle_id=handle_id,
                waiter_task_id=current_task_id,
                waiter_k=waiter_k,
                waiter_store=dict(ctx.store),
            )

        dequeue_result = yield _SchedulerDequeueTask()
        # dequeue_result is (None, current_store) when empty, or full tuple when task available
        if dequeue_result[0] is None:
            # Queue is empty - check if waiting for external promises
            current_store = dequeue_result[1]
            external_registry = current_store.get(EXTERNAL_PROMISE_REGISTRY_KEY, {})
            if external_registry:
                # Waiting for external promise - yield to external wait handler
                # Per SPEC-CESK-004: scheduler yields WaitForExternalCompletion
                from doeff.cesk.handlers.scheduler_state_handler import (
                    EXTERNAL_COMPLETION_QUEUE_KEY,
                )

                completion_queue = current_store.get(EXTERNAL_COMPLETION_QUEUE_KEY)
                if completion_queue is None:
                    return CESKState.with_error(
                        RuntimeError("No completion queue for external promise"),
                        ctx.env,
                        current_store,
                        ctx.k,
                    )

                # Yield to external wait handler (sync or async)
                promise_id, value, error = yield WaitForExternalCompletion(queue=completion_queue)

                # Look up handle_id for this promise and mark complete
                completed_handle_id = external_registry.get(promise_id)
                if completed_handle_id is not None:
                    yield _SchedulerTaskComplete(
                        handle_id=completed_handle_id,
                        task_id=None,
                        result=value,
                        error=error,
                    )

                # Now waiter should be in queue - dequeue and resume
                dequeue_result = yield _SchedulerDequeueTask()
                if dequeue_result[0] is not None:
                    next_task_id, next_k, next_store, resume_value, resume_error, sched_store = (
                        dequeue_result
                    )
                    task_store = dict(next_store) if next_store is not None else {}
                    for key, val in sched_store.items():
                        if isinstance(key, str) and key.startswith("__scheduler_"):
                            task_store[key] = val
                    task_store[CURRENT_TASK_KEY] = next_task_id
                    if resume_error is not None:
                        return ResumeK(k=next_k, error=resume_error, store=task_store)
                    return ResumeK(k=next_k, value=resume_value, store=task_store)

                # Still no tasks? Shouldn't happen but handle gracefully
                return CESKState.with_error(
                    RuntimeError("Deadlock: external completion processed but no tasks to run"),
                    ctx.env,
                    current_store,
                    ctx.k,
                )
            return CESKState.with_error(
                RuntimeError("Deadlock: racing but no other tasks to run"),
                ctx.env,
                current_store,
                ctx.k,
            )

        next_task_id, next_k, next_store, resume_value, resume_error, current_store = dequeue_result
        task_store = dict(next_store) if next_store is not None else {}
        for key, val in current_store.items():
            if isinstance(key, str) and key.startswith("__scheduler_"):
                task_store[key] = val
        task_store[CURRENT_TASK_KEY] = next_task_id
        if resume_error is not None:
            return ResumeK(k=next_k, error=resume_error, store=task_store)
        return ResumeK(k=next_k, value=resume_value, store=task_store)

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
