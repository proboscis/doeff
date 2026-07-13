# Advanced Effects

Advanced effects for parallel execution, shared-state coordination, and runtime-aware scheduling.

## Gather Effects

Execute multiple spawned tasks in parallel and collect results.

### Gather - Parallel Tasks

```python
from doeff import do
from doeff_core_effects.scheduler import Spawn, Gather, Wait, scheduled

@do
def parallel_programs():
    t1 = yield Spawn(fetch_user(1))
    t2 = yield Spawn(fetch_user(2))
    t3 = yield Spawn(fetch_user(3))

    # Wait for all tasks in parallel
    users = yield Gather(t1, t2, t3)
    # users = [user1, user2, user3]

    return users
```

### User-Side Dict Pattern

If you need to run a dict of programs in parallel, use `Spawn` + `Gather` with dict reconstruction:

```python
@do
def parallel_dict():
    programs = {
        "user": fetch_user(123),
        "posts": fetch_posts(123),
        "comments": fetch_comments(123),
    }
    keys = list(programs.keys())
    tasks = []
    for prog in programs.values():
        tasks.append((yield Spawn(prog)))
    values = yield Gather(*tasks)
    results = dict(zip(keys, values))

    # results = {"user": ..., "posts": [...], "comments": [...]}
    return results
```

### Using Gather with Async Operations

To run async operations in parallel, wrap them with `Await` and `Spawn`:

```python
from doeff import Await, do
from doeff_core_effects.scheduler import Spawn, Gather

@do
def parallel_async():
    @do
    def fetch_data(url):
        return (yield Await(http_get(url)))

    t1 = yield Spawn(fetch_data("https://api1.example.com"))
    t2 = yield Spawn(fetch_data("https://api2.example.com"))

    results = yield Gather(t1, t2)
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

State semantics use `Get` and `Put`.

- `Get(key)`: read a required value (raises `KeyError` if missing)
- `Put(key, value)`: write a value

### Read-Modify-Write with Get + Put

Use `Get` followed by `Put` when you need to update state:

```python
from doeff import do
from doeff_core_effects import Get, Put, Tell

@do
def increment_counter():
    current = yield Get("counter")
    new_value = (current or 0) + 1
    yield Put("counter", new_value)
    yield Tell(f"Counter: {new_value}")
    return new_value
```

### Common Update Patterns

```python
from doeff import do
from doeff_core_effects import Get, Put

@do
def accumulate_result(value):
    current = yield Get("total")
    new_total = (current or 0) + value
    yield Put("total", new_total)
    return new_total
```

## Semaphore Effects

Semaphores provide cooperative concurrency control for limiting concurrent access.

- `CreateSemaphore(permits)` creates a semaphore with `permits >= 1`
- `AcquireSemaphore(sem)` acquires a permit (parks when none are available)
- `ReleaseSemaphore(sem)` releases a permit (wakes the next waiter in FIFO order)
- The returned `Semaphore` is opaque: keep the handle returned by `CreateSemaphore` and reuse that
  same object (or another Python reference to it). `Semaphore(...)` is not a supported public
  constructor and raises `TypeError`.

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
    return (yield Gather(*tasks))
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

Execute programs in the background and retrieve results later.

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
| `Gather(*tasks)` | Parallel, blocking | Wait for all spawned tasks immediately |
| `Spawn(prog)` | Background, non-blocking | Do other work while waiting |

```python
from doeff import Gather, Spawn, Wait, do

@do
def comparison():
    # Spawn all, then gather
    t1 = yield Spawn(prog1())
    t2 = yield Spawn(prog2())
    t3 = yield Spawn(prog3())
    results = yield Gather(t1, t2, t3)

    task = yield Spawn(slow_prog())
    yield do_other_work()
    result = yield Wait(task)
    return results, result
```

## Race Effect

`Race(*tasks)` resumes when the first task completes.

- First completion wins.
- If the winner fails, `Race` raises that error.
- If the winner is cancelled, `Race` raises `TaskCancelledError`.
- `Race` returns the winning value directly.

By default, non-winning siblings keep running. Cancel them explicitly when you want winner-takes-all
behavior:

```python
from doeff import Cancel, Race, Spawn, do

@do
def first_wins():
    fast = yield Spawn(fetch_fast())
    slow = yield Spawn(fetch_slow())

    value = yield Race(fast, slow)

    # Cancel remaining tasks explicitly
    yield Cancel(fast)
    yield Cancel(slow)

    return value
```

## Cancel / TaskCancelledError

Cancellation is cooperative and non-blocking:

- `yield Cancel(task)` requests cancellation and returns immediately.
- `Wait`, `Gather`, and `Race` raise `TaskCancelledError` when waiting on a cancelled task.

```python
from doeff import Cancel, Try, Spawn, TaskCancelledError, Wait, do

@do
def cancel_and_join():
    task = yield Spawn(long_running_job())
    yield Cancel(task)

    joined = yield Try(Wait(task))
    if joined.is_err() and isinstance(joined.error, TaskCancelledError):
        return "cancelled"
    return joined.value
```

## WithObserve

`WithObserve` is the canonical observation primitive. It observes yielded effects
within a scoped subtree (including child dispatch in that scope).

### Observer Contract

```python
from doeff import do

def observer(effect):
    # Observer is a plain callable that receives each effect.
    # It is called for observation only — return value is ignored.
    pass
```

### Child Propagation

`WithObserve` applies to scoped child execution contexts:

- `Gather` branches run under the observe scope.
- `Spawn` tasks created inside the scope inherit the scope.

In parallel contexts, observation order is not guaranteed.

```python
from doeff import Ask, Spawn, WithObserve, Gather, Wait, do

@do
def child():
    return (yield Ask("user"))

@do
def run_with_observe():
    seen = []
    def my_observer(effect):
        seen.append(effect)

    @do
    def body():
        t1 = yield Spawn(child())
        t2 = yield Spawn(child())
        return (yield Gather(t1, t2))

    result = yield WithObserve(my_observer, body())
    return result
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

`Delay`, `GetTime`, and `WaitUntil` are runtime-dependent. They live in the `doeff_time` package:

```python
from doeff_time import Delay, GetTime, WaitUntil
```

| Effect | Sync Runtime | Simulation Runtime | Async Runtime |
|--------|--------------|--------------------|---------------|
| `Delay` | Real blocking sleep | Advances simulated clock instantly | Real non-blocking async sleep |
| `GetTime` | Current wall-clock time | Current simulated time | Current wall-clock time |
| `WaitUntil` | Sleeps until target time | Advances sim clock to target time | Awaits until target time |

If `WaitUntil` receives a time in the past, it returns immediately.

## Combining Advanced Effects

### Gather + State Updates

```python
from doeff import Gather, Spawn, do
from doeff_core_effects import Get, Put

@do
def parallel_counter():
    yield Put("count", 0)

    @do
    def increment_task(n):
        for _ in range(n):
            current = yield Get("count")
            yield Put("count", (current or 0) + 1)
        return "done"

    t1 = yield Spawn(increment_task(100))
    t2 = yield Spawn(increment_task(100))
    t3 = yield Spawn(increment_task(100))
    yield Gather(t1, t2, t3)

    final = yield Get("count")
    return final
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
- Multiple independent spawned tasks
- Fan-out computation patterns
- Parallel data fetching

**DON'T:**
- Dependent computations (use sequential yields)
- Single program (just yield it directly)

### When to Use Get + Put

**DO:**
- State reads and updates
- Counters and accumulators
- Sequential read-modify-write flows

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

### When to Use WithObserve

**DO:**
- Observe effects in a scoped subtree for logging or tracing
- Monitor effects across parallel child branches

**DON'T:**
- Assume deterministic observation order across parallel child effects

## Summary

| Effect | Purpose | Use Case |
|--------|---------|----------|
| `Spawn(prog)` + `Gather(*tasks)` | Parallel tasks | Fan-out computation |
| `Spawn(prog)` | Background execution | Non-blocking tasks |
| `Race(*tasks)` | First-completion wait | Fastest-response selection |
| `Cancel(task)` | Cooperative task cancellation | Stop non-winning/background work |
| `WithObserve(observer, body)` | Scoped effect observation | Tracing, logging |
| `Get(key)` + `Put(key, val)` | State read and write | Shared-state updates |
| `CreateSemaphore / AcquireSemaphore / ReleaseSemaphore` | Cooperative concurrency limit | Rate limiting, mutex, pools |
| `Delay / GetTime / WaitUntil` | Runtime-aware time control (doeff_time) | Time-based workflows |

## Next Steps

- **[Async Effects](04-async-effects.md)** - Async operations with Await
- **[Cache System](07-cache-system.md)** - Persistent caching
- **[Patterns](12-patterns.md)** - Advanced patterns and best practices
