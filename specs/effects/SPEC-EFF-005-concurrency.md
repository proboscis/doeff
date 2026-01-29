# SPEC-EFF-005: Concurrency Effects

## Status: Implemented

## Summary

This specification defines the semantics for concurrency effects in doeff. The design separates **computation initiation** (`Spawn`) from **result retrieval** (`Wait`, `Race`, `Gather`), enabling flexible composition of concurrent workflows.

## Design Philosophy

### Separation of Concerns

The key insight driving this design is that concurrent programming has two distinct operations:

1. **Starting computation**: Launch work that runs independently
2. **Waiting for results**: Retrieve the outcome when needed

Previous designs conflated these operations. For example, `Gather(prog1, prog2)` both started AND waited. This limits flexibility:

```python
# OLD: Cannot do work between starting and collecting
results = yield Gather(prog1(), prog2())  # Blocks immediately

# NEW: Full control over timing
t1 = yield Spawn(prog1())
t2 = yield Spawn(prog2())
# ... do other work while t1, t2 run in background ...
results = yield Gather(t1, t2)  # Wait when ready
```

### Type Hierarchy

```
Future[T]          # Read-side handle: can be Waited, Raced, Gathered
├── Task[T]        # From Spawn: adds cancel(), is_done()

Promise[T]         # Write-side handle: complete via effects
├── future         # -> Future[T] (the read-side)

RaceResult[T]      # Result of Race effect
├── first: Future[T]           # Winner
├── value: T                   # Winner's value
└── rest: tuple[Future[T], ...]  # Losers
```

**Why separate Future and Promise?**
- `Future` is the **read-side**: consumers wait for results
- `Promise` is the **write-side**: producers resolve values via effects
- This separation enables advanced patterns like callback bridges and manual coordination
- `Task` extends `Future` with lifecycle control (cancellation, status checks)
- `Promise` is user-facing via `CreatePromise`, `CompletePromise`, `FailPromise` effects

---

## Effects Overview

### Computation Effects

| Effect | Takes | Returns | Purpose |
|--------|-------|---------|---------|
| `Spawn` | `Program[T]` | `Task[T]` | Start computation, get handle |
| `Wait` | `Future[T]` | `T` | Block until single future completes |
| `Race` | `*Future[T]` | `RaceResult[T]` | Wait for first completion |
| `Gather` | `*Future[T]` | `list[T]` | Wait for all completions |
| `Await` | `Coroutine[T]` | `T` | Bridge to Python asyncio |

### Promise Effects (User-Level Future Creation)

| Effect | Takes | Returns | Purpose |
|--------|-------|---------|---------|
| `CreatePromise` | - | `Promise[T]` | Create a new Promise/Future pair |
| `CompletePromise` | `Promise[T]`, `T` | `None` | Resolve promise with value |
| `FailPromise` | `Promise[T]`, `Exception` | `None` | Resolve promise with error |

---

## Future[T] Protocol

### Definition

```python
from typing import Protocol, TypeVar, Generic

T = TypeVar('T', covariant=True)

class Future(Protocol[T]):
    """Read-side handle for a computation that will produce a value."""
    pass  # Marker protocol - operations via effects
```

`Future` is intentionally minimal. All operations on futures go through effects:

- `yield Wait(future)` → get value
- `yield Race(f1, f2, f3)` → race multiple
- `yield Gather(f1, f2, f3)` → collect all

### Why Not Methods?

We avoid `future.get()` or `future.result()` because:
1. **Effect tracking**: All blocking operations should be explicit effects
2. **Handler flexibility**: Handlers can implement waiting differently
3. **Testability**: Effects can be intercepted; direct method calls cannot

---

## Task[T] (extends Future[T])

### Definition

```python
@dataclass(frozen=True)
class Task(Generic[T]):
    """Handle for a spawned computation. Extends Future with lifecycle control."""
    _id: TaskId
    _handle: Any  # Runtime-specific handle
    
    def cancel(self) -> "CancelEffect":
        """Request cancellation. Returns effect to yield."""
        return CancelEffect(self)
    
    def is_done(self) -> bool:
        """Non-blocking check if task completed (success, error, or cancelled)."""
        ...
```

### Lifecycle States

```
PENDING ──spawn──> RUNNING ──success──> COMPLETED
                      │
                      ├──error────> FAILED
                      │
                      └──cancel───> CANCELLED
```

### Cancel Semantics

```python
task = yield Spawn(long_running())

# Request cancellation (non-blocking)
yield task.cancel()

# Wait for cancellation to take effect (raises TaskCancelledError)
try:
    result = yield Wait(task)
except TaskCancelledError:
    print("Task was cancelled")
```

**Important**: `cancel()` is cooperative. The task will be cancelled at its next yield point.

---

## Promise[T] and Promise Effects

Promise enables **user-level Future creation** independent of `Spawn`. This is useful for:
- Bridging callback-based APIs to Future-based code
- Manual coordination between concurrent tasks
- Creating Futures that are resolved by external events

### Promise Definition

```python
@dataclass
class Promise(Generic[T]):
    """Write-side handle for completing a Future."""
    _future: Future[T]
    
    @property
    def future(self) -> Future[T]:
        """Get the read-side Future that can be Waited, Raced, or Gathered."""
        return self._future
```

### CreatePromise Effect

```python
@dataclass(frozen=True)
class CreatePromiseEffect(EffectBase):
    """Create a new Promise/Future pair."""
    pass

def CreatePromise() -> Effect:
    """Create a Promise. Returns Promise[T] with promise.future as the Future."""
    ...
```

**Semantics**:
```python
@do
def example():
    promise = yield CreatePromise()
    # promise.future is a Future[T] that can be passed to Wait, Race, Gather
    # promise itself is used with CompletePromise/FailPromise to resolve
    return promise
```

### CompletePromise Effect

```python
@dataclass(frozen=True)
class CompletePromiseEffect(EffectBase):
    """Resolve a Promise with a success value."""
    promise: Promise[Any]
    value: Any

def CompletePromise(promise: Promise[T], value: T) -> Effect:
    """Complete the promise with a value. Anyone waiting on promise.future receives value."""
    ...
```

**Semantics**:
```python
@do
def example():
    promise = yield CreatePromise()
    # ... later ...
    yield CompletePromise(promise, 42)
    # Anyone doing `yield Wait(promise.future)` now receives 42
```

### FailPromise Effect

```python
@dataclass(frozen=True)
class FailPromiseEffect(EffectBase):
    """Resolve a Promise with an error."""
    promise: Promise[Any]
    error: BaseException

def FailPromise(promise: Promise[T], error: BaseException) -> Effect:
    """Fail the promise with an error. Anyone waiting on promise.future raises error."""
    ...
```

**Semantics**:
```python
@do
def example():
    promise = yield CreatePromise()
    # ... later ...
    yield FailPromise(promise, ValueError("something went wrong"))
    # Anyone doing `yield Wait(promise.future)` now raises ValueError
```

### Promise Lifecycle

```
CreatePromise() ──> PENDING ──CompletePromise──> RESOLVED (value)
                       │
                       └──FailPromise──> REJECTED (error)
```

**Important constraints**:
- A Promise can only be completed **once** (either complete or fail)
- Attempting to complete an already-completed Promise raises `RuntimeError`
- The Future returned by `promise.future` is the same object throughout

### Use Cases

#### 1. Callback Bridge

Bridge callback-based APIs to Future-based code:

```python
@do
def callback_to_future(register_callback: Callable[[Callable], None]) -> Program[T]:
    """Convert a callback-based API to a Future."""
    promise = yield CreatePromise()
    
    def on_result(value):
        # This would be called from the callback
        # In practice, you'd need IO effect to register
        pass
    
    register_callback(on_result)
    return promise.future  # Caller can Wait on this
```

#### 2. Manual Coordination

Coordinate multiple tasks with a shared signal:

```python
@do
def coordinator():
    """One task signals others via Promise."""
    signal = yield CreatePromise()
    
    @do
    def worker(worker_id: int):
        # Wait for signal before proceeding
        value = yield Wait(signal.future)
        yield Log(f"Worker {worker_id} received: {value}")
        return worker_id * value
    
    # Start workers - they all wait on the same signal
    t1 = yield Spawn(worker(1))
    t2 = yield Spawn(worker(2))
    t3 = yield Spawn(worker(3))
    
    # Do some setup work
    yield Delay(0.1)
    
    # Signal all workers at once
    yield CompletePromise(signal, 10)
    
    # Gather results
    results = yield Gather(t1, t2, t3)
    return results  # [10, 20, 30]
```

#### 3. External Event Resolution

Create a Future that's resolved by an external event:

```python
@do
def wait_for_external_event():
    promise = yield CreatePromise()
    
    # Store promise somewhere accessible to external code
    yield Put("pending_promise", promise)
    
    # Wait for external resolution
    result = yield Wait(promise.future)
    return result

# Later, external code can resolve:
@do
def external_resolver():
    promise = yield Get("pending_promise")
    yield CompletePromise(promise, "external value")
```

#### 4. Multiple Waiters

Multiple tasks can wait on the same Future:

```python
@do
def shared_future_example():
    promise = yield CreatePromise()
    
    @do
    def waiter(name: str):
        value = yield Wait(promise.future)
        return f"{name} got {value}"
    
    t1 = yield Spawn(waiter("A"))
    t2 = yield Spawn(waiter("B"))
    t3 = yield Spawn(waiter("C"))
    
    # All waiters block until promise is completed
    yield CompletePromise(promise, "shared")
    
    results = yield Gather(t1, t2, t3)
    # ["A got shared", "B got shared", "C got shared"]
```

---

## Spawn Effect

### Definition

```python
@dataclass(frozen=True)
class SpawnEffect(EffectBase):
    """Spawn execution of a program and return a Task handle."""
    program: ProgramLike
    preferred_backend: SpawnBackend | None = None
    options: dict[str, Any] = field(default_factory=dict)
```

### Semantics

```python
@do
def example():
    # Start computation, get handle immediately
    task = yield Spawn(background_work())
    
    # Task runs in background while we continue
    intermediate = yield do_other_work()
    
    # Wait for result when needed
    result = yield Wait(task)
    return (intermediate, result)
```

### Store Semantics

**Isolated store, shared environment cache**

| Resource | Behavior | Rationale |
|----------|----------|-----------|
| Store (Get/Put) | Snapshot at spawn (isolated) | Mutations shouldn't leak between tasks |
| Env cache (Ask resolutions) | Shared across tasks | Read-only, expensive to recompute |

**Store isolation**:
- Child task gets a copy of the store when spawned
- Child's modifications don't affect parent
- Parent's later modifications don't affect child

```python
@do
def example():
    yield Put("counter", 0)
    
    @do
    def increment():
        c = yield Get("counter")
        yield Put("counter", c + 1)
        return c + 1
    
    task = yield Spawn(increment())
    yield Put("counter", 100)  # Parent's change
    
    result = yield Wait(task)
    # result == 1 (child saw counter=0)
    
    final = yield Get("counter")
    # final == 100 (parent's value, not affected by child)
```

**Shared env cache**:
- Environment (Ask effect) resolutions are cached
- Cache is shared across parent and all spawned tasks
- Avoids redundant dependency resolution in parallel workloads

```python
@do
def example():
    # First resolution - cache miss, computes value
    db = yield Ask("database")
    
    @do
    def child():
        # Cache hit - reuses parent's resolved value
        db = yield Ask("database")
        return db.query("SELECT ...")
    
    # All children share the env cache
    t1 = yield Spawn(child())
    t2 = yield Spawn(child())
    t3 = yield Spawn(child())
    
    results = yield Gather(t1, t2, t3)
    # "database" resolved once, reused 4 times
```

**Rationale**: Store isolation prevents accidental mutation leaks. Shared env cache is safe (read-only) and efficient (no redundant lookups).

### Error Handling

Exceptions in spawned tasks are stored until `Wait`:

```python
@do
def example():
    @do
    def failing():
        raise ValueError("oops")
    
    task = yield Spawn(failing())
    # No error yet - task runs in background
    
    yield do_other_work()  # This completes fine
    
    # Error surfaces here
    result = yield Safe(Wait(task))
    # result.is_err() == True
    # result.error is ValueError("oops")
```

---

## Wait Effect

### Definition

```python
@dataclass(frozen=True)
class WaitEffect(EffectBase):
    """Wait for a Future to complete and return its value."""
    future: Future[Any]
```

### Semantics

```python
@do
def example():
    task = yield Spawn(computation())
    
    # Block until task completes
    result = yield Wait(task)
    return result
```

### Error Propagation

If the awaited future completed with an error, `Wait` raises that error:

```python
@do
def example():
    task = yield Spawn(failing_program())
    
    try:
        result = yield Wait(task)
    except ValueError as e:
        # Error from failing_program propagates here
        return f"Failed: {e}"
```

### Cancelled Tasks

Waiting on a cancelled task raises `TaskCancelledError`:

```python
@do
def example():
    task = yield Spawn(long_running())
    yield task.cancel()
    
    try:
        result = yield Wait(task)
    except TaskCancelledError:
        return "Task was cancelled"
```

---

## Race Effect

### Definition

```python
@dataclass(frozen=True)
class RaceEffect(EffectBase):
    """Wait for the first Future to complete."""
    futures: Tuple[Future[Any], ...]
```

### RaceResult Type

```python
@dataclass(frozen=True)
class RaceResult(Generic[T]):
    """Result of a Race effect."""
    first: Future[T]              # The Future that completed first
    value: T                      # The winner's result value
    rest: tuple[Future[T], ...]   # Remaining Futures (losers)
```

### Semantics

```python
@do
def example():
    t1 = yield Spawn(fast_service())
    t2 = yield Spawn(slow_service())
    t3 = yield Spawn(medium_service())
    
    # Wait for first completion
    result = yield Race(t1, t2, t3)
    
    # Access winner and value directly
    print(f"Winner: {result.first}, Value: {result.value}")
    
    # Cancel losers - direct access via result.rest
    for loser in result.rest:
        yield loser.cancel()
    
    return result.value
```

### Return Value

`Race` returns a `RaceResult[T]` with three fields:
- `first`: The `Future` that completed first
- `value`: The winner's result value
- `rest`: Tuple of remaining futures (losers), ready for cancellation

**Why this structure?**
- Named fields are clearer than positional tuples
- `rest` provides direct access to losers without manual filtering
- Type-safe and self-documenting

### Error Handling

If the first future to complete is an error, that error propagates:

```python
@do
def example():
    t1 = yield Spawn(quick_failure())  # Fails fast
    t2 = yield Spawn(slow_success())
    
    # quick_failure completes first (with error)
    # Race raises that error
    result = yield Race(t1, t2)  # Raises!
```

To handle errors gracefully, wrap individual programs in `Safe`:

```python
@do
def example():
    t1 = yield Spawn(Safe(quick_failure()))
    t2 = yield Spawn(Safe(slow_success()))
    
    result = yield Race(t1, t2)
    # result.value is Result[T], not T
    # Can check result.value.is_ok() / result.value.is_err()
```

### Loser Semantics

**Losers continue running unless explicitly cancelled.**

This is intentional:
- User may want to use loser results later
- Automatic cancellation adds complexity and magic
- Explicit cancellation is predictable

---

## Gather Effect

### Definition

```python
@dataclass(frozen=True)
class GatherEffect(EffectBase):
    """Wait for all Futures to complete and collect results."""
    futures: Tuple[Future[Any], ...]
```

### Semantics

```python
@do
def example():
    t1 = yield Spawn(service_a())
    t2 = yield Spawn(service_b())
    t3 = yield Spawn(service_c())
    
    # Wait for all to complete
    results = yield Gather(t1, t2, t3)
    # results == [result_a, result_b, result_c]
    # Order matches input order, not completion order
```

### Result Ordering

Results are returned in the **same order as futures were passed**, regardless of completion order:

```python
@do
def example():
    t_slow = yield Spawn(slow())   # Takes 3s
    t_fast = yield Spawn(fast())   # Takes 1s
    
    results = yield Gather(t_slow, t_fast)
    # results == [slow_result, fast_result]
    # Even though fast completed first
```

### Error Handling

**First error fails Gather (fail-fast)**

```python
@do
def example():
    t1 = yield Spawn(success())
    t2 = yield Spawn(failure())  # Will raise
    t3 = yield Spawn(success())
    
    results = yield Gather(t1, t2, t3)  # Raises error from t2
```

To collect all results including errors:

```python
@do
def example():
    t1 = yield Spawn(Safe(operation1()))
    t2 = yield Spawn(Safe(operation2()))
    t3 = yield Spawn(Safe(operation3()))
    
    results = yield Gather(t1, t2, t3)
    # results: list[Result[T]]
    # Can inspect each for success/failure
```

### Empty Gather

`Gather()` with no futures returns `[]` immediately.

---

## Await Effect (asyncio Bridge)

### Definition

```python
@dataclass(frozen=True)
class AwaitEffect(EffectBase):
    """Await a Python coroutine (asyncio bridge)."""
    awaitable: Awaitable[Any]
```

### Purpose

`Await` is **only** for bridging to Python's asyncio ecosystem. It is NOT for doeff-native futures.

```python
import aiohttp

@do
def fetch_data():
    # Bridge to asyncio
    async with aiohttp.ClientSession() as session:
        response = yield Await(session.get("https://api.example.com"))
        data = yield Await(response.json())
    return data
```

### When to Use Await vs. Wait

| Use `Await` | Use `Wait` |
|-------------|-----------|
| Python coroutines (`async def`) | doeff `Future`/`Task` |
| `asyncio.sleep()` | Spawned programs |
| `aiohttp`, `httpx` calls | `yield Spawn(...)` results |
| Third-party async libraries | doeff-native concurrency |

### Combining with Spawn

To gather multiple coroutines:

```python
@do
def parallel_fetches():
    # Wrap coroutines in Await, then Spawn
    t1 = yield Spawn(Await(fetch_url("https://a.com")))
    t2 = yield Spawn(Await(fetch_url("https://b.com")))
    
    results = yield Gather(t1, t2)
    return results
```

**Why this works**: In doeff, an effect IS a program. `Await(coro)` is a single-effect program that can be spawned.

### Runtime Support

| Runtime | `Await` Support |
|---------|----------------|
| `AsyncRuntime` | Full support via `asyncio.create_task` |
| `SyncRuntime` | NOT supported (raises unhandled effect) |
| `SimulationRuntime` | NOT supported |

---

## Runtime Support Matrix

| Effect | AsyncRuntime | SyncRuntime | SimulationRuntime |
|--------|--------------|-------------|-------------------|
| `Spawn` | asyncio.create_task | Cooperative scheduler | Cooperative scheduler |
| `Wait` | asyncio Future.result | Cooperative scheduling | Cooperative scheduling |
| `Gather` | asyncio.gather (parallel) | Cooperative (interleaved) | Cooperative (interleaved) |
| `Race` | asyncio.wait FIRST_COMPLETED | Cooperative scheduling | Cooperative scheduling |
| `Await` | Native await | NOT supported | NOT supported (mock) |

### SyncRuntime: Cooperative Scheduler Design

`SyncRuntime` implements `Spawn`, `Wait`, `Gather`, and `Race` via a **cooperative task scheduler**—no asyncio, no threads for task execution.

#### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    SyncRuntime Scheduler                         │
│                                                                  │
│   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐       │
│   │ Ready Queue │     │ Blocked Map │     │ Timer Pool  │       │
│   │ [t1,t2,t3]  │     │ t4 → t1     │     │ (threads)   │       │
│   └─────────────┘     │ t5 → [t2,t3]│     └─────────────┘       │
│          │            └─────────────┘            │               │
│          ▼                   ▲                   │               │
│   ┌──────────────────────────┴───────────────────┘              │
│   │                 Scheduler Loop                               │
│   │                                                              │
│   │   while ready_queue or blocked_tasks:                       │
│   │       task = ready_queue.pop()                              │
│   │       effect = task.send(last_result)                       │
│   │                                                              │
│   │       match effect:                                         │
│   │           Spawn(prog) → enqueue new task, return Task       │
│   │           Wait(fut)   → block task until fut completes      │
│   │           Gather(fs)  → block until all complete            │
│   │           Race(fs)    → block until first completes         │
│   │           Delay(s)    → timer thread, block task            │
│   │           Return(v)   → complete task, unblock waiters      │
│   │           other       → handle normally                     │
│   └─────────────────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────────┘
```

#### Key Properties

1. **Single-threaded task execution**: All `@do` generator code runs on one thread. No locks needed in user code.

2. **Cooperative scheduling**: Tasks yield control at every `yield` statement. Scheduler picks next ready task.

3. **Deterministic ordering**: For pure compute (no `Delay`), execution order is deterministic and reproducible.

4. **Timer threads for Delay**: `Delay` effects use background threads for timing, but task resumption happens on the main thread.

```python
# Example: Interleaved execution
@do
def task_a():
    yield Log("A1")
    yield Log("A2")
    return "A"

@do
def task_b():
    yield Log("B1")
    yield Log("B2")
    return "B"

@do
def main():
    t1 = yield Spawn(task_a())
    t2 = yield Spawn(task_b())
    return (yield Gather(t1, t2))

# Possible log order (round-robin): A1, B1, A2, B2
# Exact order depends on scheduler policy
```

#### Why Not asyncio?

The cooperative scheduler provides:
- **No asyncio dependency**: Works in sync-only environments
- **Deterministic testing**: Control over execution order
- **Simpler mental model**: No event loop complexity
- **Portable**: Same code runs on SyncRuntime and SimulationRuntime

asyncio is still preferred when:
- You need true parallelism (kernel-level I/O multiplexing)
- You're integrating with async Python libraries (aiohttp, etc.)
- You need `Await` effect for Python coroutines

---

## Composition Examples

### Race with Timeout

```python
@do
def with_timeout(program: Program[T], timeout_seconds: float) -> Program[T | None]:
    work = yield Spawn(program)
    timer = yield Spawn(delay_then_none(timeout_seconds))
    
    result = yield Race(work, timer)
    
    # Cancel the loser
    for loser in result.rest:
        yield loser.cancel()
    
    if result.first is timer:
        return None  # Timed out
    else:
        return result.value

@do
def delay_then_none(seconds: float):
    yield Delay(seconds)
    return None
```

### Parallel with Progress

```python
@do
def parallel_with_progress(programs: list[Program[T]]) -> Program[list[T]]:
    tasks = []
    for prog in programs:
        task = yield Spawn(prog)
        tasks.append(task)
    
    results = {}
    remaining = list(tasks)
    
    while remaining:
        race_result = yield Race(*remaining)
        results[race_result.first] = race_result.value
        remaining = list(race_result.rest)  # rest is already the losers
        yield Log(f"Completed {len(results)}/{len(tasks)}")
    
    # Reorder to match input order
    return [results[t] for t in tasks]
```

### Fire and Forget

```python
@do
def fire_and_forget(program: Program[Any]) -> Program[None]:
    """Start a task but don't wait for it."""
    _ = yield Spawn(program)
    return None
```

---

## Migration Guide

### From Old Gather (Programs)

```python
# OLD
results = yield Gather(prog1(), prog2(), prog3())

# NEW
t1 = yield Spawn(prog1())
t2 = yield Spawn(prog2())
t3 = yield Spawn(prog3())
results = yield Gather(t1, t2, t3)
```

### From task.join()

```python
# OLD
task = yield Spawn(program())
result = yield task.join()

# NEW
task = yield Spawn(program())
result = yield Wait(task)
```

### From Await for Everything

```python
# OLD (using Await for doeff programs via some conversion)
result = yield Await(run_as_coroutine(program()))

# NEW (native)
task = yield Spawn(program())
result = yield Wait(task)
```

---

## Implementation Notes

### Handler Implementation Pattern

Handlers create Promise/Future pairs:

```python
async def handle_spawn(effect: SpawnEffect, state: RuntimeState) -> Task:
    promise = Promise()
    
    async def run_in_background():
        try:
            result = await execute_program(effect.program, state)
            promise.complete(result)
        except Exception as e:
            promise.fail(e)
    
    asyncio.create_task(run_in_background())
    return Task(promise.future)
```

### Wait Handler Pattern

```python
async def handle_wait(effect: WaitEffect, state: RuntimeState) -> Any:
    future = effect.future
    # Block until future completes
    while not future._is_complete:
        await asyncio.sleep(0)  # Yield to event loop
    
    if future._error:
        raise future._error
    return future._value
```

---

## Open Questions

### 1. Should Race auto-cancel losers?

**Current**: No, explicit cancellation required.
**Alternative**: `Race(..., auto_cancel=True)` option.
**Recommendation**: Keep explicit for v1, consider option later.

### 2. Should Gather support partial results on error?

**Current**: Fail-fast, no partial results.
**Alternative**: Return `list[Result[T]]` always.
**Recommendation**: Use `Safe` wrapper pattern for this use case.

### 3. Timeout as built-in?

**Current**: Compose with Race + Delay.
**Alternative**: `yield Timeout(task, seconds=5.0)`.
**Recommendation**: Composition is flexible enough for v1.

---

## Related Specifications

- SPEC-EFF-004: Intercept Semantics (how intercept applies to spawned programs)
- SPEC-EFF-100: Effect Laws (composition laws for concurrency effects)

---

## References

- Source: `doeff/effects/spawn.py`, `doeff/effects/wait.py`, `doeff/effects/race.py`, `doeff/effects/gather.py`, `doeff/effects/promise.py`
- Runtime: `doeff/cesk/runtime/async_.py`
- Tests: `tests/cesk/test_spawn.py`, `tests/cesk/test_concurrency_api.py`
