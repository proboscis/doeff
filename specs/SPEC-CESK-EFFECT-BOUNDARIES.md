# SPEC: CESK Effect Boundaries

## Overview

doeff is an Effect monad processor with a stack of handlers. This spec clarifies the boundary between:

1. **Effects** - Handled inside doeff by the handler stack
2. **Escaped Effects** - Leave doeff's control, handled by external runtime

These are fundamentally different concepts that should not be conflated.

---

## Motivation: Why Does `Suspended` (to be renamed `PythonAsyncSyntaxEscape`) Exist?

At first glance, having a `Suspended` type in CESK step results looks like a code smell. Why does a pure effect interpreter need a special escape hatch?

**The answer: Python's `async def` syntax.**

This is NOT a design flaw. It's a necessary accommodation for Python's syntax-level async/await construct.

### Key Insight

```
Almost all monads can be handlers:
  State, Reader, Writer, Error, IO, Future, List, ...
  → All handled INSIDE doeff. No escaping needed.

Only one case requires escaping:
  User wants to integrate with THEIR asyncio event loop.
  → Python's `await` is SYNTAX. Cannot be hidden.
  → Must expose async interface. Must yield control.
```

### The Python Syntax Problem

```python
# This is SYNTAX, not a function call:
result = await some_coroutine()
         ↑
         Cannot be abstracted away.
         Cannot be hidden inside a sync function.
         Infects all callers with async.

# asyncio is COOPERATIVE:
# - Must yield control to get results
# - Cannot busy-wait (blocks event loop)
# - Cannot nest run_until_complete (loop already running)
```

### Conclusion

`Suspended` (to be renamed `PythonAsyncSyntaxEscape`) exists **specifically for Python's async/await integration**. It is:

- **NOT** a general monad escape hatch
- **NOT** needed for State, Reader, Writer, Error, IO, etc.
- **NOT** a design flaw

It is:

- **Specifically** for users who want `AsyncRunner` (opt-in loop integration)
- **A workaround** for Python's syntax-level async/await
- **ONLY for AsyncRunner** - SyncRunner never sees it (handlers handle Await directly)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         doeff boundary                           │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Handler Stack (Effect Monad)                              │  │
│  │                                                            │  │
│  │    queue_handler                                           │  │
│  │      └─► scheduler_handler                                 │  │
│  │            └─► async_effects_handler                       │  │
│  │                  └─► core_handler                          │  │
│  │                        └─► User Program                    │  │
│  │                                                            │  │
│  │  Effects bubble UP through handlers until caught.          │  │
│  │  Handled effects stay INSIDE doeff.                        │  │
│  └───────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                              │ Unhandled effect                  │
│                              ▼                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  step() : CESKState → StepResult                           │  │
│  │                                                            │  │
│  │  StepResult = Done | Failed | CESKState | EscapedEffect    │  │
│  │                                            ↑               │  │
│  │                                   leaves doeff             │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
                               │
                               │ PythonAsyncSyntaxEscape
                               │ (ONLY for AsyncRunner)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                       External Runtime                           │
│                                                                  │
│  AsyncRunner: receives PythonAsyncSyntaxEscape, awaits          │
│  SyncRunner: NEVER sees escape (handlers handle Await directly) │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Concepts

### 1. Effects (Handled Inside doeff)

Effects are yielded by programs and caught by handlers in the stack.

```python
@do
def my_program():
    x = yield Get("key")      # caught by core_handler
    yield Put("key", x + 1)   # caught by core_handler
    t = yield Spawn(task())   # caught by scheduler_handler
    yield Wait(t)             # caught by scheduler_handler
    return x
```

All these effects are **handled inside doeff**. They never leave the system.

### 2. Escaped Effects (Leave doeff)

Some effects cannot be handled by any handler in the stack. They must escape to the caller.

```python
@do
def my_program():
    result = yield Await(some_coroutine())  # escapes to runtime
    return result
```

`Await` produces an effect that no handler can fully process. It must be:
1. Awaited in an asyncio event loop, OR
2. Run in a thread with its own event loop

This decision is **outside doeff's control**. The effect escapes.

### 3. PythonAsyncSyntaxEscape (Currently Named: Suspended)

When an effect escapes for async integration, `step()` returns `PythonAsyncSyntaxEscape`:

```python
@dataclass(frozen=True)
class PythonAsyncSyntaxEscape:  # currently named: Suspended
    """
    Escape hatch for Python's async/await SYNTAX.
    
    This type exists because:
    - Python's `await` is SYNTAX, not a function call
    - Cannot be hidden inside a sync function
    - Cooperative scheduling requires yielding control
    
    NOT a general monad escape. Specifically for:
    - User chose AsyncRunner (opt-in loop integration)
    - Effect contains an awaitable that must run in user's event loop
    
    Could theoretically be named FreeBind (it IS the Bind case of
    Free monad), but that suggests generality. This is specifically
    for Python's async/await syntax limitation.
    
    If user uses SyncRunner, this escapes to thread pool instead
    of user's loop, hiding all async from the user.
    """
    awaitable: Any                                  # coroutine to await
    resume: Callable[[Any, Store], CESKState]       # continuation
    resume_error: Callable[[BaseException], CESKState]
    store: Store | None = None
```

From a theoretical perspective, this is the **Free monad** Bind case:

```
step : CESKState → Free[ExternalOp, StepResult]

where:
  Free.Pure(result)     = Done | Failed | CESKState
  Free.Bind(op, cont)   = EscapedEffect(payload=op, resume=cont)
```

The runtime is the **interpreter** for this Free monad.

---

## TaskWaitingForCallback vs PythonAsyncSyntaxEscape

These are **completely different** concepts that should not be conflated.

### TaskWaitingForCallback: Handler-Internal Scheduling

Purpose: Scheduler handler tracks tasks waiting for callbacks (handler-internal, invisible to CESK).

```
┌─────────────────────────────────────────────────────────────────┐
│  TaskWaitingForCallback Flow (all inside scheduler handler)      │
│                                                                  │
│  1. Handler receives effect (e.g., RayRemoteEffect)              │
│  2. Handler submits work to its executor (ray.remote())          │
│  3. Handler creates a Future, registers callback                 │
│  4. Handler returns Future to user                               │
│  5. User does: yield Wait(future)                                │
│  6. Scheduler handler creates TaskWaitingForCallback             │
│  7. Scheduler switches to next task (returns CESKState)          │
│  8. Callback fires → scheduler wakes task                        │
│                                                                  │
│  Everything stays INSIDE scheduler handler.                      │
│  CESK only sees CESKState → CESKState → Done                     │
└─────────────────────────────────────────────────────────────────┘
```

### PythonAsyncSyntaxEscape: Effect Leaves doeff for Async

Purpose: Signal that an effect cannot be handled internally.

```
┌─────────────────────────────────────────────────────────────────┐
│  EscapedEffect Flow                                              │
│                                                                  │
│  1. User does: yield Await(coroutine)                            │
│  2. Effect bubbles through handler stack                         │
│  3. No handler can fully handle it                               │
│  4. step() returns EscapedEffect(payload=coroutine, resume=...)  │
│  5. Effect LEAVES doeff                                          │
│  6. Runtime awaits coroutine in its event loop                   │
│  7. Runtime calls resume(value) to continue                      │
│                                                                  │
│  Effect escapes doeff. Runtime owns execution.                   │
└─────────────────────────────────────────────────────────────────┘
```

### Comparison

| Aspect | TaskWaitingForCallback | PythonAsyncSyntaxEscape |
|--------|------------------------|-------------------------|
| Level | Handler-internal | CESK result |
| Visible to CESK? | No | Yes |
| Purpose | Track suspended tasks | Escape for Python async |
| Who manages | Scheduler handler | Runner |
| Stays inside doeff? | Yes | No |
| Python async? | No | **Yes** |

---

## Scheduler Handler: Internal Task Management

### Key Insight: Scheduling is Handler-Internal

The CESK machine should be **simple** - it just steps. All scheduling complexity belongs in the **scheduler handler**.

```
CESK Machine (simple):
  step(state) → Done | Failed | CESKState | PythonAsyncSyntaxEscape
  
  No knowledge of:
    - Task queues
    - Suspended tasks  
    - Callbacks
    - External executors

Scheduler Handler (owns scheduling):
  - Manages ready queue, waiting set
  - Handles Spawn, Wait, Race, Gather
  - Registers callbacks for external executors
  - Switches between tasks
  - Returns CESKState for next task
```

### TaskWaitingForCallback (Handler-Internal, NOT CESK)

When a task needs to wait for an external callback, the **scheduler handler** tracks this internally:

```python
@dataclass
class TaskWaitingForCallback:
    """
    HANDLER-INTERNAL: Scheduler tracks tasks waiting for callbacks.
    
    NOT a CESK concept. NOT returned by step().
    Only exists inside scheduler handler's internal state.
    
    Renamed from SuspendOn to make clear:
    - Task: it's about a task, not CESK state
    - Waiting: suspended, not blocking
    - ForCallback: external executor will signal completion
    """
    task_id: TaskId
    callback_id: CallbackId
    continuation: Kontinuation
    env: Environment
    store: Store
```

### Scheduler Handler Flow

```
┌─────────────────────────────────────────────────────────────┐
│  Scheduler Handler (internal state)                          │
│                                                              │
│  ready_queue: [TaskA, TaskB, TaskC]                          │
│  waiting: {                                                  │
│      TaskD: TaskWaitingForCallback(..., callback_id=123),    │
│      TaskE: TaskWaitingForCallback(..., callback_id=456),    │
│  }                                                           │
│  callbacks: {                                                │
│      123: (ray_future, [TaskD]),                             │
│      456: (dask_future, [TaskE]),                            │
│  }                                                           │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Handler Receives Wait Effect

```python
# Inside scheduler handler:

def handle_wait(self, effect: Wait, ctx: HandlerContext):
    future = effect.future
    
    if future.is_done():
        # Already complete - return value immediately
        return ContinueValue(future.result)
    
    # Not done - suspend this task, switch to next
    self.waiting[ctx.task_id] = TaskWaitingForCallback(
        task_id=ctx.task_id,
        callback_id=future.callback_id,
        continuation=ctx.k,
        env=ctx.env,
        store=ctx.store,
    )
    
    # Register to wake when future completes
    self.callbacks[future.callback_id].append(ctx.task_id)
    
    # Switch to next ready task
    next_task = self.ready_queue.pop()
    return ContinueWithTask(next_task)  # Returns CESKState
```

### Callback Fires → Wake Task

```python
def on_callback(self, callback_id: CallbackId, result: Any):
    # Find waiting tasks
    for task_id in self.callbacks[callback_id]:
        waiting = self.waiting.pop(task_id)
        
        # Move back to ready queue with result
        self.ready_queue.append(ResumeTask(
            task_id=task_id,
            value=result,
            continuation=waiting.continuation,
            env=waiting.env,
            store=waiting.store,
        ))
```

### CESK Stays Simple

```
CESK step() only returns:
  - Done(value)                   → finished
  - Failed(error)                 → error
  - CESKState                     → keep stepping (scheduler provides next task)
  - PythonAsyncSyntaxEscape       → escape for Python async (ONLY special case)

NO scheduling concepts leak to CESK level.
TaskWaitingForCallback is INVISIBLE to CESK.
```

### TaskWaitingForCallback vs PythonAsyncSyntaxEscape

| Aspect | TaskWaitingForCallback | PythonAsyncSyntaxEscape |
|--------|------------------------|-------------------------|
| Level | Handler-internal | CESK result |
| Visible to CESK? | No | Yes |
| Purpose | Track suspended tasks | Escape for Python async |
| Who manages | Scheduler handler | Runner |
| Completion signal | Callback | await returns |
| Blocking? | No - other tasks continue | N/A - escapes doeff |

---

## Fundamental Primitives: Future/Wait/Race/Gather

`TaskWaitingForCallback` enables these fundamental scheduling primitives:

### The Primitives

```python
Future[T]   # Handle to a deferred computation
Wait(f)     # Suspend current task until f resolves, return value
Race(fs)    # Suspend until FIRST of fs resolves, return (index, value)
Gather(fs)  # Suspend until ALL of fs resolve, return [values]
```

### Implementation (Handler-Internal)

```
Spawn(task) → Future[T]:
  1. Scheduler creates new task, adds to ready_queue
  2. Returns Future (just a TaskId handle)
  3. Current task continues immediately
  4. NO TaskWaitingForCallback needed

Wait(future) → T:
  1. Scheduler checks if future's task is done
     - If done: return value immediately (ContinueValue)
     - If not done:
         a. Create TaskWaitingForCallback for current task
         b. Store in scheduler's waiting set
         c. Switch to next task from ready_queue
         d. Return CESKState for next task
  2. Other tasks continue stepping (CESK keeps going)
  3. When future completes, callback wakes waiting task
  4. Scheduler moves task back to ready_queue
  5. Task resumes with value

Race([f1, f2, f3]) → (int, T):
  1. Register TaskWaitingForCallback to wake on ANY completion
  2. Switch to next task (return CESKState)
  3. Other tasks continue stepping
  4. First to complete → callback wakes task
  5. Resume with (winner_index, value)

Gather([f1, f2, f3]) → [T, T, T]:
  1. Register TaskWaitingForCallback to wake when ALL complete
  2. Switch to next task (return CESKState)
  3. Other tasks continue stepping
  4. Last to complete → callback wakes task
  5. Resume with [v1, v2, v3]
```

### Key Insight: Handler-Internal, CESK Stays Simple

```
Future/Wait/Race/Gather are HANDLER-INTERNAL.

- Scheduler handler manages task queue
- TaskWaitingForCallback tracks suspended tasks
- Scheduler returns CESKState (next task to step)
- CESK just keeps stepping - no scheduling knowledge
- NO Python async escape needed

CESK sees: CESKState, CESKState, CESKState, Done
CESK doesn't know tasks are switching!
```

### When External Executor is Involved

```
RaySpawn(fn, args) → Future[T]:
  1. Handler submits to ray.remote()
  2. Handler registers callback for ray completion
  3. Handler creates internal Future
  4. Returns Future to user

Wait(future) → T:
  1. Same as before - TaskWaitingForCallback created
  2. Scheduler switches to next task (returns CESKState)
  3. Other tasks continue
  4. Ray completes → callback fires → task wakes

The ray part is HIDDEN inside the handler.
User just sees Future/Wait.
CESK just sees CESKState after CESKState.
```

---

## Problem: `_make_suspended_from_suspend_on` (Current Code)

The current code has a function `_make_suspended_from_suspend_on` that conflates two unrelated concepts:

```python
# step.py (CURRENT - PROBLEMATIC)
if isinstance(result, SuspendOn):
    return _make_suspended_from_suspend_on(result)  # → Suspended (escape)
```

**This is WRONG.** With the correct architecture:

1. **TaskWaitingForCallback** (replacing `SuspendOn`)
   - Handler-internal, invisible to CESK
   - Scheduler manages task queue internally
   - Returns CESKState (next task), NOT a special CESK result
   - NO conversion to anything

2. **PythonAsyncSyntaxEscape** (replacing `Suspended`)
   - Handler returns this DIRECTLY when escape needed
   - CESK returns it to runner
   - NO conversion from anything

### The Fix

```python
# WRONG (current): SuspendOn leaks to CESK, gets converted
if isinstance(result, SuspendOn):
    return _make_suspended_from_suspend_on(result)

# CORRECT: Scheduling is handler-internal
# Scheduler handler manages TaskWaitingForCallback internally
# Scheduler returns CESKState (next task to step)
# CESK never sees scheduling concepts

# If handler wants Python async escape:
if isinstance(result, PythonAsyncSyntaxEscape):
    return result  # Pass through to runner
```

### Correct Architecture

```
Scheduling (handler-internal):
  → Scheduler uses TaskWaitingForCallback internally
  → Scheduler switches tasks, returns CESKState
  → CESK just steps - no scheduling knowledge

Python async escape (CESK-level):
  → Handler returns PythonAsyncSyntaxEscape DIRECTLY
  → CESK returns it to runner
  → Runner awaits (or thread pool)

NO CONVERSION between them. They're unrelated.
```

---

## Terminology

### Rename Decision

We considered:
- `Suspended` → `FreeBind` or `EscapedEffect`

**Decision: Rename to `PythonAsyncSyntaxEscape`.**

`FreeBind` would be theoretically accurate (it IS the Bind case of Free monad), but misleading. It suggests a general escape mechanism when really it's **specifically for Python's async/await syntax**.

`PythonAsyncSyntaxEscape` makes it impossible to misunderstand:
- `Python` - this is a Python-specific issue
- `Async` - related to async/await
- `Syntax` - it's a syntax-level limitation
- `Escape` - escapes doeff's control

### Types

```python
class TaskWaitingForCallback:
    """
    HANDLER-INTERNAL: Scheduler tracks suspended tasks.
    
    NOT a CESK concept. NOT visible to step().
    Only exists inside scheduler handler's internal state.
    
    Renamed from SuspendOn to make purpose clear.
    """
    task_id: TaskId
    callback_id: CallbackId
    continuation: Kontinuation
    env: Environment
    store: Store

class PythonAsyncSyntaxEscape:  # currently named: Suspended
    """
    Escape hatch for Python's async/await SYNTAX.
    
    Returned by step() when user chose AsyncRunner and an effect
    requires awaiting in the user's event loop.
    
    This is NOT a general monad escape. It exists because:
    - Python's `await` is SYNTAX, not abstractable
    - Cooperative scheduling requires yielding control
    - User explicitly opted into loop integration
    
    If user uses SyncRunner, awaitables run in thread pool
    and user never sees async.
    """
    awaitable: Any                  # coroutine for user's loop
    awaitables: dict                # multi-task: {id: awaitable}
    resume: Callable[[Any, Store], CESKState]
    resume_error: Callable[[BaseException], CESKState]
    store: Store
```

### The Distinction

| Type | Level | Purpose | Visible to CESK? |
|------|-------|---------|------------------|
| `TaskWaitingForCallback` | Handler-internal | Track suspended tasks | No |
| `PythonAsyncSyntaxEscape` | CESK result | Escape for Python async | Yes |

---

## Runner vs Runtime (Migration in Progress)

### Distinction

| Concept | Handlers | Status |
|---------|----------|--------|
| **Runtime** | Hardcoded handlers | Legacy, being phased out |
| **Runner** | User provides handlers | New abstraction |

```python
# OLD: Runtime has hardcoded handlers
runtime = AsyncRuntime()  # handlers built-in
result = await runtime.run(program)

# NEW: Runner + explicit handlers
runner = AsyncRunner()
result = await runner.run(
    program,
    handlers=[core_handler, scheduler_handler, PythonAsyncLoopHandler]
)
```

### Migration Direction

We are gradually migrating from `Runtime` to `Runner + Handler`:
- Drop hardcoded handler stacks
- Make handler composition explicit
- Runners become thin loops over CESK + escape handling

---

## Runner Difference: PythonAsyncSyntaxEscape is ONLY for AsyncRunner

**IMPORTANT: PythonAsyncSyntaxEscape is ONLY for AsyncRunner.**

SyncRunner should NEVER see `PythonAsyncSyntaxEscape`. For SyncRunner, handlers
must handle `Await` effects directly (e.g., by running awaitables in a thread pool).

**Do NOT share handlers between SyncRunner and AsyncRunner.** Each runner type
should use handlers appropriate for its execution model.

```python
class SyncRunner:
    """
    Steps until Done/Failed. NEVER sees PythonAsyncSyntaxEscape.
    Handlers must handle Await directly. Returns plain T.
    """

    def run(self, program: Program[T], handlers: list[Handler]) -> T:
        state = self.init(program, handlers)
        while True:
            match self.step(state):
                case Done(v): return v
                case Failed(e): raise e
                case CESKState() as s: state = s
                # NO PythonAsyncSyntaxEscape case - handlers handle Await directly


class AsyncRunner:
    """
    Steps until Done/Failed. Handles PythonAsyncSyntaxEscape via await.
    Returns async T.
    """

    async def run(self, program: Program[T], handlers: list[Handler]) -> T:
        state = self.init(program, handlers)
        while True:
            match self.step(state):
                case Done(v): return v
                case Failed(e): raise e
                case CESKState() as s: state = s
                case PythonAsyncSyntaxEscape() as escape:
                    # Await in user's loop - THIS is why run() is async
                    result = await escape.awaitable
                    state = escape.resume(result, escape.store)
```

**Key distinction:**
- `SyncRunner`: Handlers handle Await directly (e.g., sync_await_handler runs in thread)
- `AsyncRunner`: python_async_handler produces PythonAsyncSyntaxEscape, runner awaits

Handlers are provided by user, not hardcoded. Use different handlers for each runner type.

---

## Handlers That Produce PythonAsyncSyntaxEscape

**PythonAsyncSyntaxEscape is ONLY for AsyncRunner.** Only handlers designed for
AsyncRunner should produce it.

### Handler Separation by Runner Type

```python
# FOR AsyncRunner: produces PythonAsyncSyntaxEscape
class python_async_handler:
    """
    Handler for Await effect - FOR AsyncRunner ONLY.

    Produces PythonAsyncSyntaxEscape for:
    - Await(coroutine) - await any Python coroutine

    AsyncRunner awaits the escape in the user's event loop.
    """
    ...

# FOR SyncRunner: handles Await directly (no escape)
class sync_await_handler:
    """
    Handler for Await effect - FOR SyncRunner ONLY.

    Handles Await by running awaitable in thread pool.
    Does NOT produce PythonAsyncSyntaxEscape.
    """
    
    NOTE: Delay/WaitUntil are NOT primitives.
          User can: yield Await(asyncio.sleep(seconds))
    """
    ...
```

### The Only Escape-Producing Effect

```python
class Await(EffectBase):
    """
    Await a Python coroutine.
    
    Handler returns PythonAsyncSyntaxEscape DIRECTLY.
    Runner then either:
    - awaits in user's loop (AsyncRunner)
    - runs in thread pool (SyncRunner)
    
    Examples:
        yield Await(some_async_function())
        yield Await(asyncio.sleep(1.0))  # instead of Delay
        yield Await(aiohttp.get(url))
    """
    awaitable: Coroutine
```

### Handler Produces Escape, Runner Handles It

```
User Program
    │
    │ yield Await(coroutine)
    ▼
┌─────────────────────────────────────────┐
│  PythonAsyncLoopHandler                 │
│                                         │
│  Receives: Await(coroutine)             │
│  Returns: PythonAsyncSyntaxEscape       │
│           (DIRECTLY, no conversion)     │
│                                         │
└─────────────────────────────────────────┘
    │
    │ step() returns PythonAsyncSyntaxEscape
    ▼
┌─────────────────────────────────────────┐
│  Runner                                 │
│                                         │
│  SyncRunner:  thread pool execution     │
│  AsyncRunner: await in user's loop      │
│                                         │
└─────────────────────────────────────────┘
```

### Handler Names (Rename Plan)

| Current Name | Proposed Name | Purpose |
|--------------|---------------|---------|
| `async_effects_handler` | `PythonAsyncLoopHandler` | Handles Await → produces PythonAsyncSyntaxEscape |
| `queue_handler` | `SchedulerStateHandler` | Manages scheduler's internal state in store |
| `scheduler_handler` | `TaskSchedulerHandler` | High-level Spawn/Wait/Gather/Race |
| `core_handler` | (keep) | Get/Put/Ask/Tell/Log |

### Scheduler-Internal Effects (Rename Plan)

The effects in `queue.py` are **scheduler-internal** - used between handlers, NOT for user programs.

Current confusing names suggest general-purpose queue:

```
queue.py (CONFUSING - suggests general queue)
├── QueueAdd
├── QueuePop  
├── QueueIsEmpty
├── RegisterWaiter
├── TaskComplete
├── CreateTaskHandle
├── SuspendForIOEffect
└── ...
```

Proposed explicit names:

```
scheduler_internal.py (CLEAR - scheduler internals)
├── _SchedulerEnqueueTask      (was QueueAdd)
├── _SchedulerDequeueTask      (was QueuePop)
├── _SchedulerQueueEmpty       (was QueueIsEmpty)
├── _SchedulerRegisterWaiter   (was RegisterWaiter)
├── _SchedulerTaskComplete     (was TaskComplete)
├── _SchedulerCreateTaskHandle (was CreateTaskHandle)
├── _SchedulerGetCurrentTaskId (was GetCurrentTaskId)
├── _SchedulerGetTaskStore     (was GetCurrentTaskStore)
├── _SchedulerUpdateTaskStore  (was UpdateTaskStore)
├── _SchedulerSetTaskSuspended (was SetTaskSuspended)
├── _SchedulerTaskCompleted    (was TaskCompletedEffect)
├── _SchedulerCancelTask       (was CancelTask)
├── _SchedulerIsTaskDone       (was IsTaskDone)
├── _SchedulerCreatePromise    (was CreatePromiseHandle)
└── _SchedulerGetTaskResult    (was GetTaskResult)

REMOVED (conflated concerns):
├── SuspendForIOEffect    → REMOVE (Python async is separate from scheduler)
├── AddPendingIO          → REMOVE (Python async is separate from scheduler)
├── GetPendingIO          → REMOVE (Python async is separate from scheduler)
├── RemovePendingIO       → REMOVE (Python async is separate from scheduler)
└── ResumePendingIO       → REMOVE (Python async is separate from scheduler)

These "pending IO" effects conflated Python async with scheduler.
In desired architecture: Python async escape goes DIRECTLY to runner.
```

The underscore prefix signals:
- These are INTERNAL (not for user programs)
- They're part of scheduler implementation
- Users should use high-level effects: `Spawn`, `Wait`, `Gather`, `Race`

---

## Effect Categorization

### User-Facing Effects (Public API)

Effects that user programs yield directly:

```
Core Effects (core_handler):
├── Ask(key)              - read from environment
├── Local(env, program)   - run with modified environment
├── Get(key)              - read from state
├── Put(key, value)       - write to state
├── Modify(key, fn)       - modify state
├── Tell(message)         - append to log
├── Listen(program)       - capture log output
├── Safe(program)         - catch errors
├── IO(fn)                - perform IO
├── GetTime()             - current time
├── CacheGet/Put/Delete   - cache operations
└── Pure(value)           - return pure value

Scheduling Effects (TaskSchedulerHandler):
├── Spawn(program)        - create new task, returns Task[T]
├── Wait(task_or_promise) - wait for completion
├── Gather([waitables])   - wait for all
├── Race([waitables])     - wait for first
├── CreatePromise()       - create promise, returns Promise[T]
├── CompletePromise(p, v) - resolve promise
└── FailPromise(p, err)   - reject promise

Python Async Effects (PythonAsyncLoopHandler):
└── Await(coroutine)      - await Python coroutine

NOTE: Delay/WaitUntil are NOT needed as primitives.
      User can: yield Await(asyncio.sleep(seconds))
```

### Handler-Internal Effects (Private)

Effects used between handlers - NOT for user programs:

```
Scheduler Internals (_Scheduler*):
├── _SchedulerEnqueueTask
├── _SchedulerDequeueTask
├── _SchedulerRegisterWaiter
├── _SchedulerTaskComplete
└── ... (see full list above)

NOTE: _SchedulerSuspendForIO should NOT exist in desired architecture.
      Python async escape goes DIRECTLY to PythonAsyncSyntaxEscape,
      NOT through scheduler. These are separate concerns.
```

### Effect → Handler Mapping (DESIRED Architecture)

```
┌─────────────────────────────────────────────────────────────────┐
│  User Program                                                    │
│    yield Spawn(task)                                             │
│    yield Wait(future)                                            │
│    yield Get("key")                                              │
│    yield Await(coro)                                             │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  core_handler                                                    │
│    Get, Put, Ask, Tell, etc. → handled here, return value       │
│    Other effects → bubble up                                     │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  TaskSchedulerHandler                                            │
│    Spawn → create task, add to queue via _Scheduler* effects    │
│    Wait  → if done: return value                                │
│            if not: TaskWaitingForCallback, switch task          │
│    Gather, Race → similar pattern                               │
│    Other effects → bubble up                                     │
│                                                                  │
│    NOTE: Manages task switching INTERNALLY                       │
│          Returns CESKState (next task), NOT special result      │
│          CESK machine just sees state after state               │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  SchedulerStateHandler                                           │
│    _Scheduler* effects → manage state in store                  │
│    Unhandled → error                                             │
└─────────────────────────────────────────────────────────────────┘


For Python Async (SEPARATE path - only if user wants loop integration):

┌─────────────────────────────────────────────────────────────────┐
│  User Program                                                    │
│    yield Await(coro)                                             │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  PythonAsyncLoopHandler                                          │
│    Await(coro) → return PythonAsyncSyntaxEscape DIRECTLY        │
│                                                                  │
│    NO scheduler involvement. Direct escape.                      │
│    (User can: yield Await(asyncio.sleep(s)) for delays)         │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  CESK step() returns PythonAsyncSyntaxEscape                     │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Runner                                                          │
│    SyncRunner:  run in thread pool                              │
│    AsyncRunner: await in user's loop                            │
└─────────────────────────────────────────────────────────────────┘
```

**Key difference from current implementation:**
- `PythonAsyncLoopHandler` returns `PythonAsyncSyntaxEscape` DIRECTLY
- NOT via `_SchedulerSuspendForIO`
- Scheduler is NOT involved in Python async escape
- These are SEPARATE concerns

### User Composes Handler Stack (DESIRED)

With the Runner abstraction, users explicitly compose handlers:

```python
# Minimal: just core effects (no scheduling, no async)
result = sync_runner.run(program, handlers=[
    core_handler,
])

# With scheduling (Spawn/Wait/Gather/Race)
result = sync_runner.run(program, handlers=[
    SchedulerStateHandler,      # outermost: manages scheduler state
    TaskSchedulerHandler,       # handles Spawn/Wait/Gather/Race
    core_handler,               # innermost: handles Get/Put/Ask/etc
])

# With Python async loop integration (opt-in)
result = await async_runner.run(program, handlers=[
    SchedulerStateHandler,      # outermost
    TaskSchedulerHandler,
    PythonAsyncLoopHandler,     # ← produces PythonAsyncSyntaxEscape
    core_handler,               # innermost
])

# WITHOUT Python async (no PythonAsyncLoopHandler)
# SyncRunner and AsyncRunner behave IDENTICALLY
result = sync_runner.run(program, handlers=[
    SchedulerStateHandler,
    TaskSchedulerHandler,
    core_handler,
])
```

**Key insight:** `PythonAsyncLoopHandler` is **opt-in**:
- Include it → `Await` effects escape via `PythonAsyncSyntaxEscape`
- Don't include it → `Await` effects are unhandled (error) or handled differently
- Without it, `SyncRunner` and `AsyncRunner` behave identically

---

## Summary Table

| Concept | Level | Purpose |
|---------|-------|---------|
| Effect | CESK | Normal program operation |
| Handler | CESK | Catches and processes effects |
| `TaskWaitingForCallback` | Handler-internal | Scheduler tracks suspended tasks |
| `PythonAsyncSyntaxEscape` | CESK result | Escape for Python async syntax |
| Runner | External | Loops over step(), handles escapes |

**Key insights:**

1. `PythonAsyncSyntaxEscape` exists specifically for Python's `async/await` syntax. It is NOT a general escape mechanism.

2. **`PythonAsyncSyntaxEscape` is ONLY for AsyncRunner:**
   - `SyncRunner`: NEVER sees PythonAsyncSyntaxEscape. Handlers handle Await directly.
   - `AsyncRunner`: Handlers produce PythonAsyncSyntaxEscape, runner awaits.

3. **Do NOT share handlers between SyncRunner and AsyncRunner.** Each runner type needs its own handlers appropriate for its execution model.

4. We are migrating from `Runtime` (hardcoded handlers) to `Runner` (user provides handlers).

**Removed legacy code:**
- `SuspendOn` type - removed entirely
- `_make_suspended_from_suspend_on` - removed entirely
- Scheduling is handler-internal (invisible to CESK)
- Python async escape is returned directly by handler (only for AsyncRunner)

---

## Correct Abstraction: Free Monad over ExternalOp

### The Type Signature

```
step : CESKState → Free[ExternalOp, StepResult]

where:
  Free[F, A] = Pure A | Bind (F X) (X → Free[F, A])
  
run : Free[F, A] → (∀X. F X → M X) → M A
                    └────────────┘
                    natural transformation
                    (interpreter)
```

### Free Monad Structure

```
data Free f a where
  Pure :: a → Free f a
  Bind :: f x → (x → Free f a) → Free f a

In our case:
  f = ExternalOp
  a = StepResult

step : State → Free ExternalOp StepResult

  Pure(Done v)     = computation finished
  Pure(Failed e)   = computation failed  
  Pure(State s)    = continue stepping
  Bind(op, cont)   = need external help, then continue
```

### Natural Transformation (Interpreter)

```
interpret : (∀x. F x → M x) → Free F a → M a

The first argument is a natural transformation:
  - Maps each operation in F to an effect in M
  - Preserves structure (naturality)

interpret nat (Pure a)       = M.pure(a)
interpret nat (Bind op cont) = nat(op) >>= (interpret nat . cont)
```

### Concrete Interpreters

```
ExternalOp x = operation that produces x externally

Async interpreter:
  nat : ExternalOp x → Awaitable x
  nat (AwaitOp aw) = aw

IO interpreter:
  nat : ExternalOp x → IO x  
  nat (AwaitOp aw) = runInThread(aw)

Pure interpreter:
  nat : ExternalOp x → Identity x
  nat _ = error "no external ops in pure mode"
```

---

## Implementation: Generator as Lazy Free Monad

Python generators provide a lazy representation of the Free monad.

### The Correspondence

```
@do
def my_program():
    x = yield Get("key")     # effect 1
    y = yield Await(coro)    # effect 2 (escapes)
    z = yield Put("k", x+y)  # effect 3
    return z

This IS the Free monad:

Bind(Get("key"), λx →
  Bind(Await(coro), λy →
    Bind(Put("k", x+y), λz →
      Pure(z))))

But built LAZILY via generator protocol.
```

### Generator ↔ Free Monad

```
Free Monad (eager)          Generator (lazy)
──────────────────          ────────────────

Pure(a)                     return a (StopIteration)

Bind(op, cont)              yield op
                            cont = gen.send(result)

The generator IS the continuation.
gen.send(x) resumes with x, produces next Bind or Pure.
```

### Step Returns One Level of Free

```
step(state) → Free[ExternalOp, StepResult]

We don't build the whole tree.
We return ONE level:

  Pure(Done v)        → done
  Pure(Failed e)      → failed
  Pure(CESKState s)   → continue stepping
  Bind(op, cont)      → need external, cont resumes generator

Where cont : X → step(resumed_state)

This is FREE MONAD in CPS / one-step-at-a-time form.
```

### Data Structure

```python
@dataclass
class StepPure:
    """Free.Pure - no external op needed for this step."""
    result: Done | Failed | CESKState

@dataclass  
class StepBind(Generic[X]):
    """Free.Bind - external op needed, then continue."""
    op: ExternalOp[X]
    cont: Generator  # suspended generator = continuation

StepFree = StepPure | StepBind
```

### The Hack: Generator as Linear Continuation

```
In pure Free monad:
  Bind(op, cont)  where cont : X → Free[F, A]

With generators:
  StepBind(op, gen)  where gen.send(x) → next StepFree

The generator IS the continuation, but:
  - It's mutable (can only be resumed once)
  - It's lazy (next step computed on send)
  - It carries the whole remaining computation

This is the "hack" - using generator as linear continuation.
```

### Interpreter Loop

```python
def interpret(nat: Callable[[ExternalOp], M], state: CESKState) -> M[Result]:
    """
    nat : ExternalOp x → M x  (natural transformation)
    
    Loop over step(), interpreting escaped ops via nat.
    """
    while True:
        free = step(state)  # → StepPure | StepBind
        
        match free:
            case StepPure(Done(v)):
                return M.pure(success(v))
            
            case StepPure(Failed(e)):
                return M.pure(failure(e))
            
            case StepPure(CESKState() as s):
                state = s
                continue
            
            case StepBind(op, cont):
                x = nat(op)        # M[X] - interpret external op
                state = cont.send(x)  # resume generator
                continue
```

---

## Summary

```
Correct structure:
  step : State → Free[ExternalOp, StepResult]

Implementation:
  Free is represented lazily via generators
  Bind.cont is a suspended generator, not a function
  gen.send(x) = apply continuation

The data structure (StepPure | StepBind) is correct.
The continuation representation (generator) is the hack.

doeff produces: Free[ExternalOp, Result]  (pure data)
Runtime is:     interpreter for Free      (nat : F ~> M)
```

This makes doeff pure. The natural transformation (interpreter) is the only place where external effects are executed.

---

## Why CESK Doesn't Need Monad Parameterization

### The Question

Should CESK be parameterized by a monad M?

```
CESK[M] where M : Monad

step : CESKState → M[StepResult]
run  : Program[T] → M[T]
```

### The Answer: No

Almost all monads can be implemented as **handlers** inside doeff:

| Monad | doeff Handler | Escapes? |
|-------|---------------|----------|
| State | Get, Put, Modify | No |
| Reader | Ask, Local | No |
| Writer | Tell, Log, Listen | No |
| Error | Safe, try/catch | No |
| IO | Handler runs IO | No |
| Future | Spawn, Wait (task queue) | No |
| List | Nondeterminism handler | No |
| ... | ... | No |

**All of these stay inside doeff.** Handlers process effects and return values. No escaping needed.

### The Exception: Async

Async is special, but **not because of semantics**:

```
Async is special because of PYTHON SYNTAX.

    result = await some_coroutine()
             ↑
             This `await` keyword is SYNTAX-LEVEL.
             You cannot hide it inside a function and return a plain value.
             The async "infects" everything above it.
```

The problem is not that async is semantically different from other monads. The problem is that Python's `async`/`await` is a syntax-level construct that cannot be abstracted over.

### The Cooperative Scheduling Problem

```
asyncio is COOPERATIVE multitasking:

1. Schedule awaitable
2. YIELD control (await)  ← MANDATORY
3. Event loop runs awaitable
4. Event loop resumes us with result

Step 2 cannot be skipped. No yield = no progress.

You cannot:
  - Busy wait (blocks loop, awaitable never runs)
  - Nest run_until_complete (loop already running)
  - Get result without yielding
```

### The Solution: Two Runners

Since async is a **syntax issue**, not a semantic one, we don't parameterize CESK. Instead, we provide two runners:

```python
class SyncRunner:
    """Everything handled internally. No async infection."""
    
    def run(self, program: Program[T], handlers: list[Handler]) -> T:
        """
        All effects handled by handlers.
        PythonAsyncSyntaxEscape? Run in thread pool.
        User gets plain T. No async.
        """
        state = self.init(program, handlers)
        while True:
            match self.step(state):
                case Done(v): return v
                case Failed(e): raise e
                case CESKState() as s: state = s
                case PythonAsyncSyntaxEscape() as escape:
                    # Run awaitable in thread with its own loop
                    result = run_in_thread_with_new_loop(escape.awaitable)
                    state = escape.resume(result, escape.store)


class AsyncRunner:
    """Opt-in: integrate with user's event loop."""
    
    async def run(self, program: Program[T], handlers: list[Handler]) -> T:
        """
        User explicitly wants their coroutines in THEIR loop.
        Fine. Then they get async back.
        """
        state = self.init(program, handlers)
        while True:
            match self.step(state):
                case Done(v): return v
                case Failed(e): raise e
                case CESKState() as s: state = s
                case PythonAsyncSyntaxEscape() as escape:
                    # Await in user's loop
                    result = await escape.awaitable
                    state = escape.resume(result, escape.store)
```

### The Key Insight

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  MOST USERS: SyncRunner                                     │
│                                                             │
│  - Use handlers for everything                              │
│  - PythonAsyncSyntaxEscape? Thread pool handles it          │
│  - run(program, handlers) → T                               │
│  - No async infection. Pure interface.                      │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  OPT-IN: AsyncRunner                                        │
│                                                             │
│  - User says: "I want my awaits in MY event loop"           │
│  - async run(program, handlers) → T                         │
│  - Async infection is USER'S CHOICE, not our imposition     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Why Not CESK[M]?

1. **Handlers cover almost everything**: State, Reader, Writer, Error, IO, Future, etc. are all handlers.

2. **Async is syntax, not semantics**: The only reason async is different is Python's `await` keyword. This is not a monad-theoretic distinction.

3. **Two runners is cleaner**: Rather than parameterizing by M, we provide:
   - `SyncRunner`: hides all complexity, returns `T`
   - `AsyncRunner`: exposes async for users who want loop integration

4. **User's choice is explicit**: If you use `AsyncRunner`, you're explicitly opting into async. It's not forced on you.

### Generator as Monad Substitute

The interpreter loop cannot use a true monad because Python generators are **linear** (single-use):

```
Haskell monad:
  (>>=) : M A → (A → M B) → M B
  
  The continuation (A → M B) is a pure function.
  Can be called multiple times.

Python generator:
  gen.send(x) → next value
  
  The generator IS the continuation.
  But it's mutable, single-use.
  Can only be resumed once.
```

So instead of returning `M[Result]`, we return a generator that the runtime loops over:

```python
def run(program) -> Generator[ExternalOp, Any, Result]:
    """
    Yields: ExternalOp (requests external handling)
    Receives: result from runtime
    Returns: final Result
    """
    state = init(program)
    while True:
        match step(state):
            case Done(v): return Ok(v)
            case Failed(e): return Err(e)
            case CESKState() as s: state = s
            case StepBind(op, resume):
                value = yield op  # pure yield, no await
                state = resume(value)
```

The runtime then loops over this generator:

```python
# Sync: blocks on each op
def run_sync(gen):
    result = None
    while True:
        try:
            op = gen.send(result)
            result = run_in_thread(op)  # blocking
        except StopIteration as e:
            return e.value

# Async: awaits in user's loop
async def run_async(gen):
    result = None
    while True:
        try:
            op = gen.send(result)
            result = await op.awaitable  # async
        except StopIteration as e:
            return e.value
```

### Summary

```
CESK is PURE. No monad parameter needed.

All monads except Async → handlers (stay inside doeff)
Async → special case (Python syntax issue)

SyncRunner:  handlers + thread pool → returns T
AsyncRunner: handlers + user's loop → returns async T (opt-in)

The async/sync split is not about monad parameterization.
It's about Python's syntax-level async/await construct.
```
