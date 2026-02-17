# Advanced Effects

Advanced effects for scheduler-level concurrency, promise-based coordination, shared-state safety, and runtime-aware time behavior.

## Scheduler Primitives (Advanced View)

These scheduler effects compose to model most concurrent workflows:

| Effect | Output | Notes |
| --- | --- | --- |
| `Spawn(program)` | `Task[T]` | Starts child task and returns immediately |
| `Wait(waitable)` | `T` | Waits for one `Task`/`Future` |
| `Gather(*waitables)` | `list[T]` | Waits for all; fail-fast on first error/cancel |
| `Race(*waitables)` | winner + value | Waits for first completion |
| `CreatePromise()` | `Promise[T]` | Internal scheduler promise |
| `CompletePromise(p, value)` | `None` | Resolves internal promise |
| `FailPromise(p, error)` | `None` | Fails internal promise |
| `CreateExternalPromise()` | `ExternalPromise[T]` | Promise completed by external code |

## Task Lifecycle and Handler Boundaries

Scheduler task lifecycle is internal to the scheduler handler:

- `Pending` -> `Running` on first schedule
- `Running` -> `Suspended` on cooperative preemption
- `Running` -> `Blocked` on `Wait`/`Gather`/`Race` when inputs are pending
- `Blocked` -> `Running` when callback wakeup fires
- terminal states: `Completed`, `Failed`, `Cancelled`

Boundary rules:

- `Wait`, `Gather`, and `Race` suspension is handler-internal.
- VM does not manage task queues or waiter sets.
- VM continues by stepping `Continue`; scheduler chooses which task to run next.
- Wakeups happen through scheduler callbacks when task/promise completion arrives.

## Gather and Race Patterns

### Gather over tasks

```python
from doeff import Gather, Spawn, do

@do
def gather_tasks():
    t1 = yield Spawn(fetch_user(1))
    t2 = yield Spawn(fetch_user(2))
    return (yield Gather(t1, t2))
```

### Gather over dynamic dict inputs

```python
from doeff import Gather, do

@do
def gather_dict(programs: dict[str, object]):
    keys = list(programs.keys())
    values = yield Gather(*programs.values())
    return dict(zip(keys, values))
```

### Gather fail-fast with `Safe`

`Gather` fails immediately on first error/cancellation. To collect partial outcomes:

```python
from doeff import Gather, Safe, Spawn, do

@do
def gather_partial():
    t1 = yield Spawn(Safe(might_fail_a()))
    t2 = yield Spawn(Safe(might_fail_b()))
    t3 = yield Spawn(Safe(might_fail_c()))
    return (yield Gather(t1, t2, t3))
```

### Race first-winner semantics

`Race` resumes when the first waitable resolves.

- first completion wins
- first error/cancel propagates
- losers keep running unless cancelled explicitly

Conceptually this is `(winner_index, value)` in argument order.

```python
from doeff import Race, Spawn, do

@do
def race_tasks():
    t1 = yield Spawn(work("slow", 0.4))
    t2 = yield Spawn(work("fast", 0.1))
    ordered = (t1, t2)

    result = yield Race(*ordered)
    return ordered.index(result.first), result.value
```

## Promise-Based Synchronization

### Internal Promise flow

```python
from doeff import CompletePromise, CreatePromise, Spawn, Wait, do

@do
def producer(promise):
    yield CompletePromise(promise, {"status": "ready"})

@do
def consumer():
    promise = yield CreatePromise()
    _ = yield Spawn(producer(promise))
    return (yield Wait(promise.future))
```

Use `FailPromise(promise, error)` for error paths.

### External Promise bridge

`CreateExternalPromise()` is the bridge when completion happens in external code (async library callback, thread, process, queue consumer).

```python
import threading
from doeff import CreateExternalPromise, Wait, do

@do
def wait_external_event():
    promise = yield CreateExternalPromise()

    def on_external_event():
        try:
            data = read_external_source()
            promise.complete(data)
        except Exception as exc:
            promise.fail(exc)

    threading.Thread(target=on_external_event, daemon=True).start()
    return (yield Wait(promise.future))
```

## Spawn + Wait Orchestration

```python
from doeff import Spawn, Tell, Wait, do

@do
def background_workflow():
    task = yield Spawn(expensive_computation())

    yield Tell("running independent work while child runs")
    local = yield quick_operation()

    remote = yield Wait(task)
    return local, remote
```

## State Effects in Concurrent Programs

State semantics use `Get`, `Put`, and `Modify`.

- `Get(key)`: read a required value (raises `KeyError` if missing)
- `Put(key, value)`: write a value
- `Modify(key, fn)`: atomic read-modify-write update

### Atomic updates with `Modify`

```python
from doeff import Modify, Tell, do

@do
def increment_counter():
    new_value = yield Modify("counter", lambda current: (current or 0) + 1)
    yield Tell(f"Counter: {new_value}")
    return new_value
```

### `Get`/`Put` vs `Modify`

```python
from doeff import Get, Modify, Put, do

@do
def unsafe_increment():
    count = yield Get("counter")
    yield Put("counter", count + 1)

@do
def safe_increment():
    return (yield Modify("counter", lambda current: (current or 0) + 1))
```

## Semaphore Effects

Semaphores provide cooperative concurrency limits.

- `CreateSemaphore(permits)` creates a semaphore with `permits >= 1`
- `AcquireSemaphore(sem)` acquires a permit (parks when none available)
- `ReleaseSemaphore(sem)` releases a permit (wakes next waiter in FIFO order)

```python
from doeff import (
    AcquireSemaphore,
    CreateSemaphore,
    Gather,
    ReleaseSemaphore,
    Spawn,
    Wait,
    do,
)

@do
def worker(sem, idx):
    yield AcquireSemaphore(sem)
    try:
        return yield process_item(idx)
    finally:
        yield ReleaseSemaphore(sem)

@do
def run_bounded():
    sem = yield CreateSemaphore(3)
    tasks = []
    for i in range(10):
        tasks.append((yield Spawn(worker(sem, i))))
    return (yield Gather(*[Wait(t) for t in tasks]))
```

## Time Effects Runtime Matrix

`Delay`, `GetTime`, and `WaitUntil` depend on runtime.

| Effect | Sync Runtime | Simulation Runtime | Async Runtime |
| --- | --- | --- | --- |
| `Delay` | real blocking sleep | advances simulated clock instantly | real non-blocking async sleep |
| `GetTime` | wall-clock time | simulated time | wall-clock time |
| `WaitUntil` | sleeps until target | advances sim clock to target | awaits until target |

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

    return (yield Get("count"))
```

## Best Practices

- Use `Await` for Python awaitables; use `Wait` for scheduler waitables.
- Keep `Gather` inputs independent and wrap risky children in `Safe` when partial results matter.
- Cancel losing race tasks explicitly if they should not continue.
- Prefer promise-based signaling when producer and consumer lifetimes are decoupled.
- Always release semaphore permits in `finally` blocks.

## Summary

| Effect family | Core behavior |
| --- | --- |
| `Spawn` / `Wait` | start child work and join later |
| `Gather` | wait for all with fail-fast semantics |
| `Race` | first completion wins |
| `CreatePromise` / `CompletePromise` / `FailPromise` | internal synchronization |
| `CreateExternalPromise` | external world to scheduler bridge |
| `Modify` | atomic shared-state updates |
| Semaphores | bounded cooperative concurrency |
| `Delay` / `GetTime` / `WaitUntil` | runtime-aware time control |

## Next Steps

- **[Async Effects](04-async-effects.md)** - Await and scheduler primitives
- **[Cache System](07-cache-system.md)** - persistent caching
- **[Patterns](12-patterns.md)** - larger composition patterns
