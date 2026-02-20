# Advanced Effects

Advanced effects for parallel execution, shared-state coordination, and runtime-aware scheduling.

## Gather Effects

Execute multiple Programs in parallel and collect results.

### Gather - Parallel Programs

```python
from doeff import Gather, do

@do
def parallel_programs():
    prog1 = fetch_user(1)
    prog2 = fetch_user(2)
    prog3 = fetch_user(3)

    # Run all Programs in parallel
    users = yield Gather(prog1, prog2, prog3)
    # users = [user1, user2, user3]

    return users
```

### User-Side Dict Pattern

If you need to run a dict of Programs in parallel, use `Gather` with dict reconstruction:

```python
@do
def parallel_dict():
    programs = {
        "user": fetch_user(123),
        "posts": fetch_posts(123),
        "comments": fetch_comments(123),
    }
    keys = list(programs.keys())
    values = yield Gather(*programs.values())
    results = dict(zip(keys, values))

    # results = {"user": ..., "posts": [...], "comments": [...]}
    return results
```

### Using Gather with Async Operations

To run async operations in parallel, wrap them with `Await`:

```python
from doeff import Await, Gather, do

@do
def parallel_async():
    @do
    def fetch_data(url):
        return (yield Await(http_get(url)))

    results = yield Gather(
        fetch_data("https://api1.example.com"),
        fetch_data("https://api2.example.com"),
    )
    return results
```

### Gather Fail-Fast Semantics

`Gather` is fail-fast:

- If any gathered branch fails, `Gather` raises that error immediately.
- If any gathered branch is cancelled, `Gather` raises `TaskCancelledError`.
- Remaining branches continue unless you cancel them explicitly.
- On success, results are returned in input order.

Use `Try(...)` around child programs when you need partial results instead of fail-fast behavior.

## State Effects in Concurrent Programs

State semantics use `Get`, `Put`, and `Modify`.

- `Get(key)`: read a required value (raises `KeyError` if missing)
- `Put(key, value)`: write a value
- `Modify(key, fn)`: atomic read-modify-write update

### Atomic Updates with Modify

Use `Modify` when multiple tasks can update the same key.

```python
from doeff import Modify, Tell, do

@do
def increment_counter():
    new_value = yield Modify("counter", lambda current: (current or 0) + 1)
    yield Tell(f"Counter: {new_value}")
    return new_value
```

### Get/Put vs Modify

```python
from doeff import Get, Modify, Put, do

@do
def unsafe_increment():
    # Not atomic across tasks
    count = yield Get("counter")
    yield Put("counter", count + 1)

@do
def safe_increment():
    # Atomic read-modify-write
    return (yield Modify("counter", lambda current: (current or 0) + 1))
```

### Common Modify Patterns

```python
from doeff import Modify, do

@do
def accumulate_result(value):
    return (yield Modify("total", lambda current: (current or 0) + value))
```

## Semaphore Effects

Semaphores provide cooperative concurrency control for limiting concurrent access.

- `CreateSemaphore(permits)` creates a semaphore with `permits >= 1`
- `AcquireSemaphore(sem)` acquires a permit (parks when none are available)
- `ReleaseSemaphore(sem)` releases a permit (wakes the next waiter in FIFO order)

### Basic Pattern (Always Release in finally)

```python
from doeff import (
    AcquireSemaphore,
    CreateSemaphore,
    Gather,
    ReleaseSemaphore,
    Spawn,
    Tell,
    Wait,
    do,
)

@do
def worker(sem, worker_id):
    yield AcquireSemaphore(sem)
    try:
        yield Tell(f"Worker {worker_id} in critical section")
        return worker_id
    finally:
        yield ReleaseSemaphore(sem)

@do
def run_workers():
    sem = yield CreateSemaphore(3)  # at most 3 concurrent workers
    tasks = []
    for i in range(10):
        tasks.append((yield Spawn(worker(sem, i))))
    return (yield Gather(*[Wait(task) for task in tasks]))
```

### Semaphore Use Cases

- Rate limiting (`CreateSemaphore(N)`)
- Connection pools (`N` concurrent DB/API clients)
- Mutex-style critical sections (`CreateSemaphore(1)`)

### Semaphore Runtime Semantics

- Waiters are resumed in FIFO acquire order
- Releasing above max permits raises `RuntimeError`
- `CreateSemaphore(0)` raises `ValueError`
- Cancelled waiters are removed from the wait queue
- Permits can leak if you do not release on all code paths

## Spawn Effect

Execute Programs in the background and retrieve results later.

### Basic Spawn

```python
from doeff import Spawn, Tell, Wait, do

@do
def background_work():
    task = yield Spawn(expensive_computation())

    yield Tell("Doing other work...")
    other_result = yield quick_operation()

    background_result = yield Wait(task)
    return (other_result, background_result)
```

### Multiple Background Tasks

```python
from doeff import Spawn, Wait, do

@do
def parallel_background_work():
    task1 = yield Spawn(computation_1())
    task2 = yield Spawn(computation_2())
    task3 = yield Spawn(computation_3())

    result1 = yield Wait(task1)
    result2 = yield Wait(task2)
    result3 = yield Wait(task3)
    return [result1, result2, result3]
```

### Spawn vs Gather

| Effect | Execution | Use Case |
|--------|-----------|----------|
| `Gather(*progs)` | Parallel, blocking | Wait for all immediately |
| `Spawn(prog)` | Background, non-blocking | Do other work while waiting |

```python
from doeff import Gather, Spawn, Wait, do

@do
def comparison():
    results = yield Gather(prog1(), prog2(), prog3())

    task = yield Spawn(slow_prog())
    yield do_other_work()
    result = yield Wait(task)
    return results, result
```

## Race Effect

`Race(*waitables)` resumes when the first waitable completes.

- First completion wins.
- If the winner fails, `Race` raises that error.
- If the winner is cancelled, `Race` raises `TaskCancelledError`.
- The returned `RaceResult` exposes `first`, `value`, and `rest`.

By default, non-winning siblings keep running. Cancel them explicitly when you want winner-takes-all
behavior:

```python
from doeff import Race, Spawn, do

@do
def first_wins():
    fast = yield Spawn(fetch_fast())
    slow = yield Spawn(fetch_slow())

    result = yield Race(fast, slow)
    for loser in result.rest:
        _ = yield loser.cancel()

    return result.value
```

## Cancel / TaskCancelledError

Cancellation is cooperative and non-blocking:

- `yield task.cancel()` requests cancellation and returns immediately.
- `Wait`, `Gather`, and `Race` raise `TaskCancelledError` when waiting on a cancelled task.

```python
from doeff import Try, Spawn, TaskCancelledError, Wait, do

@do
def cancel_and_join():
    task = yield Spawn(long_running_job())
    _ = yield task.cancel()

    joined = yield Try(Wait(task))
    if joined.is_err() and isinstance(joined.error, TaskCancelledError):
        return "cancelled"
    return joined.value
```

## Intercept

`Intercept` transforms effects yielded by a program in a scoped way. The runtime does this through an
`InterceptFrame` pushed onto the continuation stack.

```python
program.intercept(transform)
# same as:
Intercept(program, transform)
```

### InterceptFrame Transform Contract

```python
from doeff import Effect, Program

def transform(effect: Effect) -> Effect | Program | None:
    ...
```

- `None`: pass through to the next transform (or the original effect).
- `Effect`: substitute with that effect (it is not re-transformed by the same `InterceptFrame`).
- `Program`: replace the effect by executing that program under the current intercept scope.
- First non-`None` transform wins.
- If a transform raises an exception, that transform exception propagates and
  Intercept evaluation fails for that step.
- Raised exceptions from the intercepted program are not rewritten by `Intercept`; they bubble through
  the `InterceptFrame` unchanged.

### Child Propagation

`InterceptFrame` is inherited by child execution contexts:

- `Gather` branches run under the parent intercept scope.
- `Spawn` tasks (including background tasks) run under the parent intercept scope.

In parallel contexts, transform observation order is not guaranteed.

```python
from doeff import Ask, AskEffect, Gather, Intercept, Program, Spawn, do

@do
def fallback_user():
    value = yield Ask("fallback_user")
    return f"user:{value}"

def transform(effect):
    if isinstance(effect, AskEffect) and effect.key == "user":
        return fallback_user()  # Program replacement
    return None

@do
def child():
    return (yield Ask("user"))

@do
def run_with_intercept():
    t1 = yield Spawn(child())
    t2 = yield Spawn(child())
    return (yield Intercept(Gather(t1, t2), transform))
```

### Chained Intercepts

```python
program.intercept(f).intercept(g)
# per yielded effect:
# 1) try f
# 2) if f returns None, try g
# 3) if both return None, run original effect
```

## Custom Control Effects with Frames

Doeff supports custom control effects by adding handlers that push custom continuation frames.
The frame contract is:

```python
from typing import Protocol

class Frame(Protocol):
    def on_value(self, value, env, store, k_rest) -> FrameResult: ...
    def on_error(self, error, env, store, k_rest) -> FrameResult: ...
```

Use this pattern to implement domain-specific control flow such as transactions, timeouts, and
retries without modifying the runtime internals.

## Time Effects Runtime Matrix

`Delay`, `GetTime`, and `WaitUntil` are runtime-dependent. Use this behavior matrix:

| Effect | Sync Runtime | Simulation Runtime | Async Runtime |
|--------|--------------|--------------------|---------------|
| `Delay` | Real blocking sleep | Advances simulated clock instantly | Real non-blocking async sleep |
| `GetTime` | Current wall-clock time | Current simulated time | Current wall-clock time |
| `WaitUntil` | Sleeps until target time | Advances sim clock to target time | Awaits until target time |

If `WaitUntil` receives a time in the past, it returns immediately.

## Combining Advanced Effects

### Gather + Modify

```python
from doeff import Gather, Get, Modify, Put, do

@do
def parallel_counter():
    yield Put("count", 0)

    @do
    def increment_task(n):
        for _ in range(n):
            yield Modify("count", lambda current: (current or 0) + 1)
        return "done"

    yield Gather(
        increment_task(100),
        increment_task(100),
        increment_task(100),
    )

    final = yield Get("count")
    return final  # 300
```

## Best Practices

### Accessing the active interpreter

If you need the active interpreter object (for example, to pass it into an external callback),
ask for the special key `__interpreter__`:

```python
from doeff import Ask, do

@do
def read_interpreter():
    interp = yield Ask("__interpreter__")
    return interp
```

This does not require adding `__interpreter__` to your environment.

### When to Use Gather

**DO:**
- Multiple independent Programs
- Fan-out computation patterns
- Parallel data fetching

**DON'T:**
- Dependent computations (use sequential yields)
- Single Program (just yield it directly)

### When to Use Modify

**DO:**
- Concurrent state updates
- Counters and accumulators
- Atomic read-modify-write flows

**DON'T:**
- Isolated single-step reads (`Get` is enough)
- Multi-key transactional workflows

### When to Use Semaphores

**DO:**
- Limit concurrent workers
- Guard critical sections
- Build bounded pools

**DON'T:**
- Skip `ReleaseSemaphore` on error paths
- Use huge permit counts as a substitute for backpressure design

### When to Use Intercept

**DO:**
- Rewrite or short-circuit selected effects in a scoped subtree
- Inject fallback programs for missing data or policy checks
- Layer transforms with clear first-match precedence

**DON'T:**
- Assume deterministic transform order across parallel child effects
- Return replacement programs that recursively trigger the same transform without a stop condition

## Summary

| Effect | Purpose | Use Case |
|--------|---------|----------|
| `Gather(*progs)` | Parallel Programs | Fan-out computation |
| `Spawn(prog)` | Background execution | Non-blocking tasks |
| `Race(*waitables)` | First-completion wait | Fastest-response selection |
| `task.cancel()` | Cooperative task cancellation | Stop non-winning/background work |
| `Intercept(prog, *transforms)` | Scoped effect transformation | Policy injection, effect rewriting |
| `Modify(key, fn)` | Atomic read-modify-write | Shared-state updates |
| `CreateSemaphore / AcquireSemaphore / ReleaseSemaphore` | Cooperative concurrency limit | Rate limiting, mutex, pools |
| `Delay / GetTime / WaitUntil` | Runtime-aware time control | Time-based workflows |

## Next Steps

- **[Async Effects](04-async-effects.md)** - Async operations with Await and Gather
- **[Cache System](07-cache-system.md)** - Persistent caching
- **[Patterns](12-patterns.md)** - Advanced patterns and best practices
