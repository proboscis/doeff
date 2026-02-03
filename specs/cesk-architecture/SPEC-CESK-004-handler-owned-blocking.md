# SPEC-CESK-004: Handler-Owned Blocking for External Completion

## Status: Draft

## Summary

Remove `WaitingForExternalCompletion` from `StepResult`. Blocking for external completion is handled through a **layered handler architecture** where:
1. `task_scheduler_handler` yields `WaitForExternalCompletion` effect (agnostic to blocking mechanism)
2. A dedicated handler converts this to the appropriate blocking mechanism (sync or async)

## Problem Statement

Currently, `WaitingForExternalCompletion` is a `StepResult` type that:

1. Is returned by `task_scheduler_handler` when no tasks are runnable but external promises are pending
2. Is checked in `run.py` which then polls the external completion queue
3. Leaks scheduler implementation details (queue management) to the run loop

```
Current (wrong):
  task_scheduler_handler
    └── returns WaitingForExternalCompletion(state=...)

  run.py
    └── sees WaitingForExternalCompletion
    └── polls completion_queue.get()
    └── processes completion
    └── resumes stepping
```

This violates separation of concerns:
- The handler knows about its queue but delegates blocking to run loop
- The run loop knows about scheduler internals (queue, completion processing)
- Two places manage what should be one concern

## Design Principles

### 1. doeff is Cooperative Scheduling

doeff has its own cooperative scheduling world. It does NOT "support" asyncio - `async_run` and `PythonAsyncSyntaxEscape` are hacks/workarounds for users who want `async def` syntax.

### 2. PythonAsyncSyntaxEscape is Exclusive

`PythonAsyncSyntaxEscape` is produced by specific handlers only:
- `python_async_syntax_escape_handler`: Converts `Await`/`Delay` effects to escapes
- `async_external_wait_handler`: Converts `WaitForExternalCompletion` to escape

No other handlers should produce this type.

### 3. Layered Handler Architecture

The scheduler should be **agnostic** to the blocking mechanism. It yields an effect (`WaitForExternalCompletion`) and a handler below it converts that to the appropriate mechanism:

```
┌─────────────────────────────────────────────────────────┐
│  task_scheduler_handler (agnostic)                      │
│                                                         │
│  if queue_empty and external_promises_pending:          │
│      yield WaitForExternalCompletion(queue)             │
│      # continues after wait resolved                    │
└───────────────────────┬─────────────────────────────────┘
                        │
        ┌───────────────┴───────────────┐
        │                               │
   sync_run                        async_run
        │                               │
        ▼                               ▼
┌───────────────────┐         ┌───────────────────────────┐
│ sync_external_    │         │ async_external_           │
│ wait_handler      │         │ wait_handler              │
│                   │         │                           │
│ return queue.get()│         │ yield PythonAsyncSyntax   │
│ (blocks thread)   │         │ Escape(run_in_executor(   │
│                   │         │   None, queue.get))       │
└───────────────────┘         └───────────────────────────┘
```

### 4. Why Different Blocking Mechanisms?

**The Problem with Direct Blocking in async_run:**

In `sync_run`, external I/O runs in background threads. Blocking on `queue.get()` works because the background thread can complete and call `queue.put()`.

In `async_run`, asyncio tasks run in the **same thread** as the CESK stepper:

```
sync_run (works):                    async_run (broken if direct block):
┌────────────────┬────────────┐      ┌─────────────────────────────────┐
│ Main Thread    │ Background │      │ Single Thread                   │
│                │ Thread     │      │                                 │
│ queue.get() ←──┼── put()   │      │ queue.get() ← BLOCKS THREAD    │
│ (blocks)       │ (completes)│      │      ↑                          │
└────────────────┴────────────┘      │      X asyncio.Task can't run   │
                                     │        (same thread blocked!)   │
                                     └─────────────────────────────────┘
```

**Solution: `run_in_executor` for async:**

```python
# async_external_wait_handler
async def wait_for_completion():
    loop = asyncio.get_event_loop()
    # Blocking queue.get() runs in thread pool
    item = await loop.run_in_executor(None, queue.get)
    return item

yield PythonAsyncSyntaxEscape(action=wait_for_completion)
```

This way:
- The blocking `get()` runs in a thread pool worker
- Main event loop is free to run asyncio tasks
- When completion arrives, executor thread returns
- No polling, no CPU waste

### 5. Blocking is Last Resort

The scheduler should only yield `WaitForExternalCompletion` when there is NO other work:

```
task_scheduler_handler scheduling loop:
  1. Runnable tasks exist? → run one (yield ResumeK)
  2. No runnable tasks, tasks waiting on external?
     → yield WaitForExternalCompletion(queue)
     → handler below handles blocking appropriately
     → process completion, wake waiter
     → goto 1
  3. No tasks at all? → done or deadlock
```

## Target Architecture

### Handler Presets

```python
sync_handlers_preset = [
    scheduler_state_handler,
    task_scheduler_handler,
    sync_external_wait_handler,   # blocks with queue.get()
    sync_await_handler,
    core_handler,
]

async_handlers_preset = [
    scheduler_state_handler,
    task_scheduler_handler,
    async_external_wait_handler,  # escapes with run_in_executor
    python_async_syntax_escape_handler,
    core_handler,
]
```

### New Effect

```python
@dataclass(frozen=True)
class WaitForExternalCompletion(EffectBase):
    """Scheduler requests blocking wait for external completion queue."""
    queue: queue.Queue
```

### StepResult After Refactor

```python
# Before
StepResult = CESKState | Done | Failed | PythonAsyncSyntaxEscape | WaitingForExternalCompletion

# After
StepResult = CESKState | Done | Failed | PythonAsyncSyntaxEscape
```

## Example Flow: `yield Await(coro)` in async_run

```
Handler stack (outer → inner):
  scheduler_state → task_scheduler → async_external_wait → python_async_escape → core

Step 1: yield Await(coro)
─────────────────────────
         │
         ▼ (handled by python_async_syntax_escape_handler)

   promise = yield CreateExternalPromise()
   yield PythonAsyncSyntaxEscape(action=create_task(fire_task))
         │
         ▼ async_run executes action, starts asyncio.Task

   result = yield Wait(promise.future)  ←── continues

Step 2: yield Wait(promise.future)
──────────────────────────────────
         │
         ▼ (handled by task_scheduler_handler)

   - Promise not complete yet
   - Register as waiter
   - Dequeue next task → queue empty
   - External promises pending

   yield WaitForExternalCompletion(queue)  ←── continues

Step 3: yield WaitForExternalCompletion(queue)
──────────────────────────────────────────────
         │
         ▼ (handled by async_external_wait_handler)

   yield PythonAsyncSyntaxEscape(
       action=lambda: loop.run_in_executor(None, queue.get)
   )
         │
         ▼ async_run executes action
         │
         │  Meanwhile, asyncio.Task from Step 1:
         │    - await coro completes
         │    - promise.complete(result)
         │    - queue.put((id, result, None))
         │
         ▼  executor's queue.get() returns

   async_external_wait_handler returns item

Step 4: Completion processed
────────────────────────────
   - task_scheduler_handler processes completion
   - Wakes waiter
   - Wait returns result
   - python_async_syntax_escape_handler returns to caller
```

## Implementation

### 1. Remove WaitingForExternalCompletion

- Delete from `doeff/cesk/result.py`
- Remove from `StepResult` type alias
- Remove handling in `doeff/cesk/run.py`

### 2. Add WaitForExternalCompletion Effect

```python
# doeff/effects/scheduler_internal.py
@dataclass(frozen=True)
class WaitForExternalCompletion(EffectBase):
    """Request blocking wait for external completion queue."""
    queue: queue.Queue
```

### 3. Update task_scheduler_handler

Replace direct blocking with effect:

```python
# When queue empty but external promises pending:
yield WaitForExternalCompletion(completion_queue)
# After handler below resolves, continue processing
```

### 4. Add sync_external_wait_handler

```python
@do
def sync_external_wait_handler(effect: EffectBase, ctx: HandlerContext):
    if isinstance(effect, WaitForExternalCompletion):
        # Direct blocking - sync context can block thread
        item = effect.queue.get()
        return item

    # Forward other effects
    result = yield effect
    return result
```

### 5. Add async_external_wait_handler

```python
@do
def async_external_wait_handler(effect: EffectBase, ctx: HandlerContext):
    if isinstance(effect, WaitForExternalCompletion):
        q = effect.queue

        async def wait_async():
            loop = asyncio.get_event_loop()
            # Returns VALUE (step() wraps to return CESKState)
            return await loop.run_in_executor(None, q.get)

        # step() wraps action to return CESKState
        # async_run awaits and gets CESKState directly
        # Handler receives the value via yield
        item = yield PythonAsyncSyntaxEscape(action=wait_async)
        return item

    # Forward other effects
    result = yield effect
    return result
```

**Note**: The handler's action returns a **value**. step() wraps it to return
**CESKState** (see SPEC-CESK-005). async_run just does `state = await result.action()`.

### 6. Update Handler Presets

```python
# doeff/cesk/run.py
sync_handlers_preset = [
    scheduler_state_handler,
    task_scheduler_handler,
    sync_external_wait_handler,
    sync_await_handler,
    core_handler,
]

async_handlers_preset = [
    scheduler_state_handler,
    task_scheduler_handler,
    async_external_wait_handler,
    python_async_syntax_escape_handler,
    core_handler,
]
```

## Success Criteria

1. `WaitingForExternalCompletion` removed from codebase
2. `run.py` has no queue-related code
3. `task_scheduler_handler` yields `WaitForExternalCompletion` (agnostic to mechanism)
4. `sync_external_wait_handler` blocks directly
5. `async_external_wait_handler` uses `run_in_executor` via escape
6. All existing tests pass
7. No behavior change from user perspective

## Migration Notes

This is an internal refactor. User-facing API and behavior should not change.

## References

- Current implementation: `doeff/cesk/handlers/task_scheduler_handler.py`
- Run loop: `doeff/cesk/run.py`
- Result types: `doeff/cesk/result.py`
- Async escape spec: `SPEC-CESK-005-simplify-async-escape.md`
