# SPEC-EFF-014: Cooperative Semaphore

## Status: Draft (Revision 1)

## Summary

A counting semaphore primitive for the doeff scheduler. The semaphore is a
cooperative concurrency primitive that limits the number of concurrent
accessors to a shared resource. A binary semaphore (`permits=1`) is
equivalent to a mutex/lock.

**Key properties**:

- **Cooperative**: Acquiring a semaphore with no available permits parks the
  task (like Wait/Gather) and resumes it when permits become available.
  No OS-level blocking.
- **Scheduler-managed**: All operations are effects handled by the scheduler
  handler, reusing existing task parking and waking infrastructure.
- **FIFO fairness**: Waiters are woken in the order they attempted to acquire.

```
Task A                              Task B                   Scheduler
──────                              ──────                   ─────────
sem = yield CreateSemaphore(1)
yield AcquireSemaphore(sem)         ...                      permits: 0
  ... critical section ...          yield AcquireSemaphore(sem)  → park Task B
yield ReleaseSemaphore(sem)                                  → wake Task B
                                    ... critical section ...
                                    yield ReleaseSemaphore(sem)  permits: 1
```

## Related Specs

| Spec | Relationship |
|------|-------------|
| SPEC-SCHED-001 | Scheduler spec. Semaphore effects are handled by the same scheduler handler. |
| SPEC-008 | Rust VM spec. Semaphore types are `#[pyclass]` structs like other scheduler types. |

## Motivation

### Missing Primitive

doeff has Spawn/Gather/Race for concurrency and Promise for synchronization,
but no mutual exclusion or resource-limiting primitive. Users who need to:

- Limit concurrent access to a shared resource (rate limiting, connection pools)
- Protect critical sections in concurrent tasks
- Coordinate producer-consumer patterns

...must currently build ad-hoc solutions using Promises.

### Design Rationale: Why Semaphore, Not Lock

A `Lock` is `Semaphore(permits=1)`. By implementing only `Semaphore`, we get:

- **Mutex**: `Semaphore(1)` — exclusive access
- **Connection pool**: `Semaphore(N)` — N concurrent accessors
- **Rate limiter**: `Semaphore(N)` — N concurrent operations

One primitive, multiple use cases. No separate Lock type needed.

## Public Types

### Semaphore

Opaque handle returned by `CreateSemaphore`. Passed to `AcquireSemaphore`
and `ReleaseSemaphore`.

```python
class Semaphore:
    """Opaque semaphore handle. Created via CreateSemaphore effect."""
    id: int  # read-only
```

## Effects

### CreateSemaphore

Creates a new semaphore with the given number of initial permits.

```python
sem = yield CreateSemaphore(permits)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `permits` | `int` | Initial (and maximum) permit count. Must be ≥ 1. |

**Returns**: `Semaphore` handle.

### AcquireSemaphore

Acquires one permit from the semaphore. If no permits are available, the
calling task is parked until a permit becomes available.

```python
yield AcquireSemaphore(sem)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `semaphore` | `Semaphore` | Semaphore handle from `CreateSemaphore`. |

**Returns**: `None` (Unit).

**Blocking behavior**: If `available_permits == 0`, the calling task
transitions to BLOCKED state. It is resumed (FIFO order) when another task
calls `ReleaseSemaphore` on the same semaphore.

### ReleaseSemaphore

Releases one permit back to the semaphore. If tasks are waiting to acquire,
the first waiter (FIFO) is woken.

```python
yield ReleaseSemaphore(sem)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `semaphore` | `Semaphore` | Semaphore handle from `CreateSemaphore`. |

**Returns**: `None` (Unit).

**Permit transfer**: When waiters exist, the permit transfers directly from
releaser to the first waiter — `available_permits` stays 0. This prevents a
third task from stealing the permit between release and waiter resumption.

## Semantics

### FIFO Fairness

Waiters are woken in the order they called `AcquireSemaphore`. No priority
inversion or starvation.

### Cancel Interaction

If a task blocked on `AcquireSemaphore` is cancelled (via `Cancel` effect),
the task is removed from the semaphore's waiter queue and receives
`TaskCancelledError`. No permit is consumed.

### Error Conditions

| Condition | Error |
|-----------|-------|
| `ReleaseSemaphore` when `available_permits == max_permits` | `RuntimeError("semaphore released too many times")` |
| `CreateSemaphore(0)` | `ValueError("permits must be >= 1")` |

### Permit Leak on Task Failure

If a task holding a permit fails or is cancelled WITHOUT calling
`ReleaseSemaphore`, the permit is **leaked**. The semaphore has no ownership
tracking and cannot auto-release.

**Recommendation**: Always use try/finally to ensure release.

### No Explicit Destroy

Semaphores are garbage-collected. The scheduler drops internal state when
the `Semaphore` handle is no longer referenced.

## Python API Examples

### Rate-Limited Workers

```python
from doeff import do, CreateSemaphore, AcquireSemaphore, ReleaseSemaphore
from doeff import Spawn, Gather, Wait, Log, Delay

@do
def rate_limited_work():
    sem = yield CreateSemaphore(3)  # max 3 concurrent workers

    tasks = []
    for i in range(10):
        task = yield Spawn(worker(sem, i))
        tasks.append(task)

    results = yield Gather(*[Wait(t) for t in tasks])
    return results

@do
def worker(sem, worker_id):
    yield AcquireSemaphore(sem)
    try:
        yield Log(f"Worker {worker_id} in critical section")
        yield Delay(0.1)
        return f"result-{worker_id}"
    finally:
        yield ReleaseSemaphore(sem)
```

### Binary Semaphore as Mutex

```python
@do
def mutex_example():
    lock = yield CreateSemaphore(1)  # binary semaphore = mutex

    task_a = yield Spawn(critical_section(lock, "A"))
    task_b = yield Spawn(critical_section(lock, "B"))

    yield Gather(Wait(task_a), Wait(task_b))

@do
def critical_section(lock, name):
    yield AcquireSemaphore(lock)
    try:
        yield Log(f"{name}: entered critical section")
        # ... exclusive access guaranteed ...
    finally:
        yield ReleaseSemaphore(lock)
```

## Future Enhancements (Out of Scope)

- **`WithSemaphore(sem, program)`**: Combinator wrapping Acquire/Release
  around a program with guaranteed cleanup.
- **Deadlock detection**: Scheduler detects cycles among semaphore waiters.
- **TryAcquireSemaphore**: Non-blocking acquire that returns `bool`.
- **Timed acquire**: `AcquireSemaphore(sem, timeout=5.0)`.
