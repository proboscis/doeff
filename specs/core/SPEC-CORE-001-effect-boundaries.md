# SPEC-CORE-001: Effect Boundaries

## Overview

doeff is an Effect monad processor with a stack of handlers. This spec clarifies the boundary between:

1. **Effects** - Handled inside doeff by the handler stack
2. **Escaped Effects** - Leave doeff's control, handled by external runtime

These are fundamentally different concepts that should not be conflated.

---

## Motivation: Why Does `PythonAsyncSyntaxEscape` Exist?

At first glance, having a special escape type in VM step results looks like a code smell. Why does a pure effect interpreter need a special escape hatch?

**The answer: Python's `async def` syntax.**

This is NOT a design flaw. It's a necessary accommodation for Python's syntax-level async/await construct.

### Key Insight

```
Almost all monads can be handlers:
  State, Reader, Writer, Error, IO, Future, List, ...
  -> All handled INSIDE doeff. No escaping needed.

Only one case requires escaping:
  User wants to integrate with THEIR asyncio event loop.
  -> Python's `await` is SYNTAX. Cannot be hidden.
  -> Must expose async interface. Must yield control.
```

### The Python Syntax Problem

```python
# This is SYNTAX, not a function call:
result = await some_coroutine()
         ^
         Cannot be abstracted away.
         Cannot be hidden inside a sync function.
         Infects all callers with async.

# asyncio is COOPERATIVE:
# - Must yield control to get results
# - Cannot busy-wait (blocks event loop)
# - Cannot nest run_until_complete (loop already running)
```

### Conclusion

`PythonAsyncSyntaxEscape` exists **specifically for Python's async/await integration**. It is:

- **NOT** a general monad escape hatch
- **NOT** needed for State, Reader, Writer, Error, IO, etc.
- **NOT** a design flaw

It is:

- **Specifically** for users who want `async_run` (opt-in loop integration)
- **A workaround** for Python's syntax-level async/await
- **ONLY for async_run** - `run` never sees it (handlers handle Await directly)

---

## Architecture

```
+------------------------------------------------------------------+
|                         doeff boundary                            |
|                                                                   |
|  +------------------------------------------------------------+  |
|  |  Handler Stack (Effect Monad)                               |  |
|  |                                                             |  |
|  |    handler_n                                                |  |
|  |      +-> handler_n-1                                        |  |
|  |            +-> ...                                          |  |
|  |                  +-> handler_1                              |  |
|  |                        +-> User Program                     |  |
|  |                                                             |  |
|  |  Effects bubble UP through handlers until caught.           |  |
|  |  Handled effects stay INSIDE doeff.                         |  |
|  +------------------------------------------------------------+  |
|                              |                                    |
|                              | Unhandled effect                   |
|                              v                                    |
|  +------------------------------------------------------------+  |
|  |  VM step : state -> step outcome                            |  |
|  |                                                             |  |
|  |  step outcome = Done | Failed | Continue | Escape           |  |
|  |                                              ^              |  |
|  |                                     leaves doeff            |  |
|  +------------------------------------------------------------+  |
|                                                                   |
+-------------------------------------------------------------------+
                               |
                               | PythonAsyncSyntaxEscape
                               | (ONLY for async_run)
                               v
+-------------------------------------------------------------------+
|                       External Runtime                            |
|                                                                   |
|  async_run: receives PythonAsyncSyntaxEscape, awaits              |
|  run:       NEVER sees escape (handlers handle Await directly)    |
|                                                                   |
+-------------------------------------------------------------------+
```

---

## Key Concepts

### 1. Effects (Handled Inside doeff)

Effects are yielded by programs and caught by handlers in the stack.

```python
from doeff import do, Get, Put, Spawn, Wait

@do
def my_program():
    x = yield Get("key")      # caught by handler
    yield Put("key", x + 1)   # caught by handler
    t = yield Spawn(task())   # caught by scheduler handler
    yield Wait(t)             # caught by scheduler handler
    return x
```

All these effects are **handled inside doeff**. They never leave the system.

### 2. Escaped Effects (Leave doeff)

Some effects cannot be handled by any handler in the stack. They must escape to the caller.

```python
from doeff import do, Await

@do
def my_program():
    result = yield Await(some_coroutine())  # escapes to runtime
    return result
```

`Await` produces an effect that no handler can fully process. It must be:
1. Awaited in an asyncio event loop, OR
2. Run in a thread with its own event loop

This decision is **outside doeff's control**. The effect escapes.

### 3. PythonAsyncSyntaxEscape

When an effect escapes for async integration, the VM step returns `Escape`:

```python
@dataclass(frozen=True)
class PythonAsyncSyntaxEscape:
    """
    Escape hatch for Python's async/await SYNTAX.

    This type exists because:
    - Python's `await` is SYNTAX, not a function call
    - Cannot be hidden inside a sync function
    - Cooperative scheduling requires yielding control

    NOT a general monad escape. Specifically for:
    - User chose async_run (opt-in loop integration)
    - Effect contains an awaitable that must run in user's event loop

    Could theoretically be named FreeBind (it IS the Bind case of
    Free monad), but that suggests generality. This is specifically
    for Python's async/await syntax limitation.
    """
    awaitable: Any                                # coroutine to await
    resume: Callable[[Any], Continue]             # continuation
    resume_error: Callable[[BaseException], Continue]
```

From a theoretical perspective, this is the **Free monad** Bind case:

```
step : state -> Free[ExternalOp, step outcome]

where:
  Free.Pure(result)     = Done | Failed | Continue
  Free.Bind(op, cont)   = Escape(payload=op, resume=cont)
```

The runtime is the **interpreter** for this Free monad.

---

## Handler-Internal Suspension vs PythonAsyncSyntaxEscape

These are **completely different** concepts that should not be conflated.

### Handler-Internal Suspension: Scheduling

Purpose: Scheduler handler tracks tasks waiting for completion (handler-internal, invisible to the VM).

```
+-------------------------------------------------------------------+
|  Handler-Internal Suspension Flow (all inside scheduler handler)  |
|                                                                   |
|  1. Handler receives effect (e.g., Wait)                          |
|  2. Handler checks if future is done                              |
|  3. If not done: suspends task, registers wakeup                  |
|  4. Handler switches to next ready task                           |
|  5. Returns Continue(next_state)                                  |
|  6. Callback fires -> scheduler wakes task                        |
|                                                                   |
|  Everything stays INSIDE scheduler handler.                       |
|  VM only sees Continue -> Continue -> Done                        |
+-------------------------------------------------------------------+
```

### PythonAsyncSyntaxEscape: Effect Leaves doeff for Async

Purpose: Signal that an effect cannot be handled internally.

```
+-------------------------------------------------------------------+
|  Escape Flow                                                      |
|                                                                   |
|  1. User does: yield Await(coroutine)                             |
|  2. Effect bubbles through handler stack                          |
|  3. No handler can fully handle it                                |
|  4. VM step returns Escape(payload=coroutine, resume=...)         |
|  5. Effect LEAVES doeff                                           |
|  6. Runtime awaits coroutine in its event loop                    |
|  7. Runtime calls resume(value) to continue                       |
|                                                                   |
|  Effect escapes doeff. Runtime owns execution.                    |
+-------------------------------------------------------------------+
```

### Comparison

| Aspect | Handler-Internal Suspension | PythonAsyncSyntaxEscape |
|--------|----------------------------|-------------------------|
| Level | Handler-internal | VM step outcome |
| Visible to VM? | No | Yes |
| Purpose | Track suspended tasks | Escape for Python async |
| Who manages | Scheduler handler | Runner |
| Stays inside doeff? | Yes | No |
| Python async? | No | **Yes** |

---

## Scheduler Handler: Internal Task Management

### Key Insight: Scheduling is Handler-Internal

The VM should be **simple** - it just steps. All scheduling complexity belongs in the **scheduler handler**.

```
VM (simple):
  step(state) -> Done | Failed | Continue | Escape

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
  - Returns Continue for next task
```

### Handler-Internal Suspension (Not a VM Concept)

When a task needs to wait for completion, the **scheduler handler** tracks this internally:

```python
@dataclass
class _SchedulerSuspendedTask:
    """
    HANDLER-INTERNAL: Scheduler tracks tasks waiting for completion.

    NOT a VM concept. NOT returned by step().
    Only exists inside scheduler handler's internal state.
    """
    task_id: TaskId
    callback_id: CallbackId
    continuation: Continuation
```

### Scheduler Handler Flow

```
+-------------------------------------------------------------+
|  Scheduler Handler (internal state)                          |
|                                                              |
|  ready_queue: [TaskA, TaskB, TaskC]                          |
|  waiting: {                                                  |
|      TaskD: _SchedulerSuspendedTask(..., callback_id=123),  |
|      TaskE: _SchedulerSuspendedTask(..., callback_id=456),  |
|  }                                                           |
+-------------------------------------------------------------+
```

### Handler Receives Wait Effect

```python
# Inside scheduler handler (current API):

def handle_wait(effect, k):
    future = effect.future

    if future.is_done():
        # Already complete - resume with value
        yield Resume(k, future.result)
    else:
        # Not done - suspend this task, switch to next
        self.waiting[ctx.task_id] = _SchedulerSuspendedTask(
            task_id=ctx.task_id,
            callback_id=future.callback_id,
            continuation=k,
        )

        # Switch to next ready task
        next_task = self.ready_queue.pop()
        yield Resume(next_task.k, next_task.value)
```

### VM Stays Simple

```
VM step() only returns:
  - Done(value)                   -> finished
  - Failed(error)                 -> error
  - Continue(state)               -> keep stepping (scheduler provides next task)
  - Escape (PythonAsyncSyntaxEscape) -> escape for Python async (ONLY special case)

NO scheduling concepts leak to VM level.
Handler-internal suspension is INVISIBLE to VM.
```

---

## Fundamental Primitives: Future/Wait/Race/Gather

Handler-internal suspension enables these fundamental scheduling primitives:

### The Primitives

```python
from doeff import Spawn, Wait, Race, Gather

Future[T]   # Handle to a deferred computation
Wait(f)     # Suspend current task until f resolves, return value
Race(fs)    # Suspend until FIRST of fs resolves, return (index, value)
Gather(fs)  # Suspend until ALL of fs resolve, return [values]
```

### Implementation (Handler-Internal)

```
Spawn(task) -> Future[T]:
  1. Scheduler creates new task, adds to ready_queue
  2. Returns Future (just a TaskId handle)
  3. Current task continues immediately
  4. NO suspension needed

Wait(future) -> T:
  1. Scheduler checks if future's task is done
     - If done: return value immediately (Resume(k, value))
     - If not done:
         a. Suspend current task internally
         b. Store in scheduler's waiting set
         c. Switch to next task from ready_queue
         d. Return Continue for next task
  2. Other tasks continue stepping (VM keeps going)
  3. When future completes, callback wakes waiting task
  4. Scheduler moves task back to ready_queue
  5. Task resumes with value

Race([f1, f2, f3]) -> (int, T):
  1. Register suspension to wake on ANY completion
  2. Switch to next task (return Continue)
  3. Other tasks continue stepping
  4. First to complete -> callback wakes task
  5. Resume with (winner_index, value)

Gather([f1, f2, f3]) -> [T, T, T]:
  1. Register suspension to wake when ALL complete
  2. Switch to next task (return Continue)
  3. Other tasks continue stepping
  4. Last to complete -> callback wakes task
  5. Resume with [v1, v2, v3]
```

### Key Insight: Handler-Internal, VM Stays Simple

```
Future/Wait/Race/Gather are HANDLER-INTERNAL.

- Scheduler handler manages task queue
- Handler-internal suspension tracks waiting tasks
- Scheduler returns Continue (next task to step)
- VM just keeps stepping - no scheduling knowledge
- NO Python async escape needed

VM sees: Continue, Continue, Continue, Done
VM doesn't know tasks are switching!
```

---

## Terminology

### Rename Decision

We considered:
- `Suspended` -> `FreeBind` or `EscapedEffect`

**Decision: Rename to `PythonAsyncSyntaxEscape`.**

`FreeBind` would be theoretically accurate (it IS the Bind case of Free monad), but misleading. It suggests a general escape mechanism when really it's **specifically for Python's async/await syntax**.

`PythonAsyncSyntaxEscape` makes it impossible to misunderstand:
- `Python` - this is a Python-specific issue
- `Async` - related to async/await
- `Syntax` - it's a syntax-level limitation
- `Escape` - escapes doeff's control

### Types

```python
class _SchedulerSuspendedTask:
    """
    HANDLER-INTERNAL: Scheduler tracks suspended tasks.

    NOT a VM concept. NOT visible to step().
    Only exists inside scheduler handler's internal state.
    """
    task_id: TaskId
    callback_id: CallbackId
    continuation: Continuation

class PythonAsyncSyntaxEscape:
    """
    Escape hatch for Python's async/await SYNTAX.

    Returned by step() when user chose async_run and an effect
    requires awaiting in the user's event loop.

    This is NOT a general monad escape. It exists because:
    - Python's `await` is SYNTAX, not abstractable
    - Cooperative scheduling requires yielding control
    - User explicitly opted into loop integration

    If user uses run (sync), awaitables run in thread pool
    and user never sees async.
    """
    awaitable: Any                  # coroutine for user's loop
    awaitables: dict                # multi-task: {id: awaitable}
    resume: Callable[[Any], Continue]
    resume_error: Callable[[BaseException], Continue]
```

### The Distinction

| Type | Level | Purpose | Visible to VM? |
|------|-------|---------|----------------|
| `_SchedulerSuspendedTask` | Handler-internal | Track suspended tasks | No |
| `PythonAsyncSyntaxEscape` | VM step outcome | Escape for Python async | Yes |

---

## Runner Difference: PythonAsyncSyntaxEscape is ONLY for async_run

**IMPORTANT: PythonAsyncSyntaxEscape is ONLY for async_run.**

`run` (sync) should NEVER see `PythonAsyncSyntaxEscape`. For sync execution, handlers
must handle `Await` effects directly (e.g., by running awaitables in a thread pool).

**Do NOT share handlers between sync and async runners.** Each runner type
should use handlers appropriate for its execution model.

```python
from doeff import run, async_run, do

@do
def my_program():
    x = yield Get("key")
    return x

# Sync runner: steps until Done/Failed. NEVER sees PythonAsyncSyntaxEscape.
# Handlers must handle Await directly. Returns plain T.
result = run(my_program(), handlers=default_handlers())

# Async runner: steps until Done/Failed.
# Handles PythonAsyncSyntaxEscape via await. Returns async T.
result = await async_run(my_program(), handlers=default_handlers())
```

### Internal Runner Loop (Conceptual)

```python
# Sync runner (conceptual loop):
def run(program, handlers):
    state = init(program, handlers)
    while True:
        match step(state):
            case Done(v): return v
            case Failed(e): raise e
            case Continue(s): state = s
            # NO Escape case - handlers handle Await directly


# Async runner (conceptual loop):
async def async_run(program, handlers):
    state = init(program, handlers)
    while True:
        match step(state):
            case Done(v): return v
            case Failed(e): raise e
            case Continue(s): state = s
            case Escape() as escape:
                # Await in user's loop - THIS is why async_run is async
                result = await escape.awaitable
                state = escape.resume(result)
```

**Key distinction:**
- `run`: Handlers handle Await directly (e.g., sync_await_handler runs in thread)
- `async_run`: python_async_handler produces PythonAsyncSyntaxEscape, runner awaits

---

## Handlers That Produce PythonAsyncSyntaxEscape

**PythonAsyncSyntaxEscape is ONLY for async_run.** Only handlers designed for
async execution should produce it.

### Handler Separation by Runner Type

```python
# FOR async_run: produces PythonAsyncSyntaxEscape
class python_async_handler:
    """
    Handler for Await effect - FOR async_run ONLY.

    Produces PythonAsyncSyntaxEscape for:
    - Await(coroutine) - await any Python coroutine

    async_run awaits the escape in the user's event loop.
    """
    ...

# FOR run (sync): handles Await directly (no escape)
class sync_await_handler:
    """
    Handler for Await effect - FOR run (sync) ONLY.

    Handles Await by running awaitable in thread pool.
    Does NOT produce PythonAsyncSyntaxEscape.
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
    - awaits in user's loop (async_run)
    - runs in thread pool (run)

    Examples:
        yield Await(some_async_function())
        yield Await(asyncio.sleep(1.0))
        yield Await(aiohttp.get(url))
    """
    awaitable: Coroutine
```

### Handler Produces Escape, Runner Handles It

```
User Program
    |
    | yield Await(coroutine)
    v
+------------------------------------------+
|  PythonAsyncLoopHandler                  |
|                                          |
|  Receives: Await(coroutine)              |
|  Returns: PythonAsyncSyntaxEscape        |
|           (DIRECTLY, no conversion)      |
|                                          |
+------------------------------------------+
    |
    | VM step returns Escape
    v
+------------------------------------------+
|  Runner                                  |
|                                          |
|  run:       thread pool execution        |
|  async_run: await in user's loop         |
|                                          |
+------------------------------------------+
```

---

## Effect Categorization

### User-Facing Effects (Public API)

Effects that user programs yield directly:

```
Core Effects (standard handlers):
+-- Ask(key)              - read from environment (Reader)
+-- Local(env, program)   - run with modified environment
+-- Get(key)              - read from state (State)
+-- Put(key, value)       - write to state
+-- Modify(key, fn)       - modify state
+-- Tell(message)         - append to log (Writer)
+-- Listen(program)       - capture log output
+-- Safe(program)         - catch errors
+-- IO(fn)                - perform IO
+-- GetTime()             - current time
+-- CacheGet/Put/Delete   - cache operations
+-- Pure(value)           - return pure value

Scheduling Effects (scheduler handler):
+-- Spawn(program)        - create new task, returns Task[T]
+-- Wait(task_or_promise) - wait for completion
+-- Gather([waitables])   - wait for all
+-- Race([waitables])     - wait for first
+-- CreatePromise()       - create promise, returns Promise[T]
+-- CompletePromise(p, v) - resolve promise
+-- FailPromise(p, err)   - reject promise

Python Async Effects (async handler):
+-- Await(coroutine)      - await Python coroutine

NOTE: Delay/WaitUntil are NOT needed as primitives.
      User can: yield Await(asyncio.sleep(seconds))
```

### Handler-Internal Effects (Private)

Effects used between handlers - NOT for user programs:

```
Scheduler Internals (_Scheduler*):
+-- _SchedulerEnqueueTask
+-- _SchedulerDequeueTask
+-- _SchedulerRegisterWaiter
+-- _SchedulerTaskComplete
+-- ... (implementation details)
```

### Effect -> Handler Mapping

```
+-------------------------------------------------------------------+
|  User Program                                                     |
|    yield Spawn(task)                                              |
|    yield Wait(future)                                             |
|    yield Get("key")                                               |
|    yield Await(coro)                                              |
+-------------------------------------------------------------------+
         |
         v
+-------------------------------------------------------------------+
|  Core handler (reader, state, writer)                             |
|    Get, Put, Ask, Tell, etc. -> handled here, resume with value   |
|    Other effects -> delegate up                                   |
+-------------------------------------------------------------------+
         |
         v
+-------------------------------------------------------------------+
|  Scheduler handler                                                |
|    Spawn -> create task, add to queue                             |
|    Wait  -> if done: resume with value                            |
|             if not: suspend internally, switch task               |
|    Gather, Race -> similar pattern                                |
|    Other effects -> delegate up                                   |
|                                                                   |
|    NOTE: Manages task switching INTERNALLY                        |
|          Returns Continue (next task), NOT special result         |
|          VM just sees state after state                           |
+-------------------------------------------------------------------+


For Python Async (SEPARATE path - only if user wants loop integration):

+-------------------------------------------------------------------+
|  User Program                                                     |
|    yield Await(coro)                                              |
+-------------------------------------------------------------------+
         |
         v
+-------------------------------------------------------------------+
|  PythonAsyncLoopHandler                                           |
|    Await(coro) -> return PythonAsyncSyntaxEscape DIRECTLY         |
|                                                                   |
|    NO scheduler involvement. Direct escape.                       |
+-------------------------------------------------------------------+
         |
         v
+-------------------------------------------------------------------+
|  VM step returns Escape                                           |
+-------------------------------------------------------------------+
         |
         v
+-------------------------------------------------------------------+
|  Runner                                                           |
|    run:       run in thread pool                                  |
|    async_run: await in user's loop                                |
+-------------------------------------------------------------------+
```

### User Composes Handler Stack

With the runner abstraction, users explicitly compose handlers:

```python
from doeff import run, async_run, default_handlers

# Simple: use default handlers (reader, state, writer, scheduler)
result = run(program(), handlers=default_handlers())

# Async: opt-in to loop integration
result = await async_run(program(), handlers=default_handlers())
```

**Key insight:** `PythonAsyncLoopHandler` is **opt-in**:
- Include it -> `Await` effects escape via `PythonAsyncSyntaxEscape`
- Don't include it -> `Await` effects are unhandled (error) or handled differently
- Without it, `run` and `async_run` behave identically

---

## Summary Table

| Concept | Level | Purpose |
|---------|-------|---------|
| Effect | VM | Normal program operation |
| Handler | VM | Catches and processes effects |
| `_SchedulerSuspendedTask` | Handler-internal | Scheduler tracks suspended tasks |
| `PythonAsyncSyntaxEscape` | VM step outcome | Escape for Python async syntax |
| Runner (`run`/`async_run`) | External | Loops over step(), handles escapes |

**Key insights:**

1. `PythonAsyncSyntaxEscape` exists specifically for Python's `async/await` syntax. It is NOT a general escape mechanism.

2. **`PythonAsyncSyntaxEscape` is ONLY for async_run:**
   - `run`: NEVER sees PythonAsyncSyntaxEscape. Handlers handle Await directly.
   - `async_run`: Handlers produce PythonAsyncSyntaxEscape, runner awaits.

3. **Do NOT share handlers between sync and async runners.** Each runner type needs its own handlers appropriate for its execution model.

---

## Correct Abstraction: Free Monad over ExternalOp

### The Type Signature

```
step : state -> Free[ExternalOp, step_outcome]

where:
  Free[F, A] = Pure A | Bind (F X) (X -> Free[F, A])

run : Free[F, A] -> (forall X. F X -> M X) -> M A
                     +---------------------+
                     natural transformation
                     (interpreter)
```

### Free Monad Structure

```
data Free f a where
  Pure :: a -> Free f a
  Bind :: f x -> (x -> Free f a) -> Free f a

In our case:
  f = ExternalOp
  a = step_outcome

step : state -> Free ExternalOp step_outcome

  Pure(Done v)        = computation finished
  Pure(Failed e)      = computation failed
  Pure(Continue s)    = continue stepping
  Bind(op, cont)      = need external help, then continue
```

### Natural Transformation (Interpreter)

```
interpret : (forall x. F x -> M x) -> Free F a -> M a

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
  nat : ExternalOp x -> Awaitable x
  nat (AwaitOp aw) = aw

IO interpreter:
  nat : ExternalOp x -> IO x
  nat (AwaitOp aw) = runInThread(aw)

Pure interpreter:
  nat : ExternalOp x -> Identity x
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

Bind(Get("key"), \x ->
  Bind(Await(coro), \y ->
    Bind(Put("k", x+y), \z ->
      Pure(z))))

But built LAZILY via generator protocol.
```

### Generator <-> Free Monad

```
Free Monad (eager)          Generator (lazy)
------------------          ----------------

Pure(a)                     return a (StopIteration)

Bind(op, cont)              yield op
                            cont = gen.send(result)

The generator IS the continuation.
gen.send(x) resumes with x, produces next Bind or Pure.
```

### Step Returns One Level of Free

```
step(state) -> Free[ExternalOp, step_outcome]

We don't build the whole tree.
We return ONE level:

  Pure(Done v)         -> done
  Pure(Failed e)       -> failed
  Pure(Continue s)     -> continue stepping
  Bind(op, cont)       -> need external, cont resumes generator

Where cont : X -> step(resumed_state)

This is FREE MONAD in CPS / one-step-at-a-time form.
```

### Data Structure

```python
@dataclass
class StepPure:
    """Free.Pure - no external op needed for this step."""
    result: Done | Failed | Continue

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
  Bind(op, cont)  where cont : X -> Free[F, A]

With generators:
  StepBind(op, gen)  where gen.send(x) -> next StepFree

The generator IS the continuation, but:
  - It's mutable (can only be resumed once)
  - It's lazy (next step computed on send)
  - It carries the whole remaining computation

This is the "hack" - using generator as linear continuation.
```

### Interpreter Loop

```python
def interpret(nat, state):
    """
    nat : ExternalOp x -> M x  (natural transformation)

    Loop over step(), interpreting escaped ops via nat.
    """
    while True:
        free = step(state)

        match free:
            case StepPure(Done(v)):
                return success(v)

            case StepPure(Failed(e)):
                return failure(e)

            case StepPure(Continue(s)):
                state = s
                continue

            case StepBind(op, cont):
                x = nat(op)           # interpret external op
                state = cont.send(x)  # resume generator
                continue
```

---

## Summary

```
Correct structure:
  step : state -> Free[ExternalOp, step_outcome]

Implementation:
  Free is represented lazily via generators
  Bind.cont is a suspended generator, not a function
  gen.send(x) = apply continuation

The data structure (StepPure | StepBind) is correct.
The continuation representation (generator) is the hack.

doeff produces: Free[ExternalOp, Result]  (pure data)
Runner is:      interpreter for Free      (nat : F ~> M)
```

This makes doeff pure. The natural transformation (interpreter) is the only place where external effects are executed.

---

## Why the VM Doesn't Need Monad Parameterization

### The Question

Should the VM be parameterized by a monad M?

```
VM[M] where M : Monad

step : state -> M[step_outcome]
run  : Program[T] -> M[T]
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
             ^
             This `await` keyword is SYNTAX-LEVEL.
             You cannot hide it inside a function and return a plain value.
             The async "infects" everything above it.
```

The problem is not that async is semantically different from other monads. The problem is that Python's `async`/`await` is a syntax-level construct that cannot be abstracted over.

### The Cooperative Scheduling Problem

```
asyncio is COOPERATIVE multitasking:

1. Schedule awaitable
2. YIELD control (await)  <- MANDATORY
3. Event loop runs awaitable
4. Event loop resumes us with result

Step 2 cannot be skipped. No yield = no progress.

You cannot:
  - Busy wait (blocks loop, awaitable never runs)
  - Nest run_until_complete (loop already running)
  - Get result without yielding
```

### The Solution: Two Runners

Since async is a **syntax issue**, not a semantic one, we don't parameterize the VM. Instead, we provide two runners:

```python
from doeff import run, async_run, default_handlers

# Sync runner: everything handled internally. No async infection.
# All effects handled by handlers.
# PythonAsyncSyntaxEscape? Run in thread pool.
# User gets plain T. No async.
result = run(program(), handlers=default_handlers())

# Async runner: opt-in to integrate with user's event loop.
# User explicitly wants their coroutines in THEIR loop.
# Then they get async back.
result = await async_run(program(), handlers=default_handlers())
```

### The Key Insight

```
+-------------------------------------------------------------+
|                                                              |
|  MOST USERS: run (sync)                                      |
|                                                              |
|  - Use handlers for everything                               |
|  - PythonAsyncSyntaxEscape? Thread pool handles it           |
|  - run(program, handlers) -> T                               |
|  - No async infection. Pure interface.                       |
|                                                              |
+--------------------------------------------------------------+
|                                                              |
|  OPT-IN: async_run                                           |
|                                                              |
|  - User says: "I want my awaits in MY event loop"            |
|  - await async_run(program, handlers) -> T                   |
|  - Async infection is USER'S CHOICE, not our imposition      |
|                                                              |
+--------------------------------------------------------------+
```

### Why Not VM[M]?

1. **Handlers cover almost everything**: State, Reader, Writer, Error, IO, Future, etc. are all handlers.

2. **Async is syntax, not semantics**: The only reason async is different is Python's `await` keyword. This is not a monad-theoretic distinction.

3. **Two runners is cleaner**: Rather than parameterizing by M, we provide:
   - `run`: hides all complexity, returns `T`
   - `async_run`: exposes async for users who want loop integration

4. **User's choice is explicit**: If you use `async_run`, you're explicitly opting into async. It's not forced on you.

### Generator as Monad Substitute

The interpreter loop cannot use a true monad because Python generators are **linear** (single-use):

```
Haskell monad:
  (>>=) : M A -> (A -> M B) -> M B

  The continuation (A -> M B) is a pure function.
  Can be called multiple times.

Python generator:
  gen.send(x) -> next value

  The generator IS the continuation.
  But it's mutable, single-use.
  Can only be resumed once.
```

So instead of returning `M[Result]`, we return a generator that the runtime loops over:

```python
def run_loop(program):
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
            case Continue(s): state = s
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
The VM is PURE. No monad parameter needed.

All monads except Async -> handlers (stay inside doeff)
Async -> special case (Python syntax issue)

run:       handlers + thread pool -> returns T
async_run: handlers + user's loop -> returns async T (opt-in)

The async/sync split is not about monad parameterization.
It's about Python's syntax-level async/await construct.
```
