# 21. Semaphore Effects

Semaphores provide cooperative concurrency control in doeff. They let you cap concurrent work,
protect critical sections, and coordinate access to finite resources without blocking OS threads.

## Table of Contents

- [Overview / Motivation](#overview--motivation)
- [Effect Definitions](#effect-definitions)
- [Usage Patterns](#usage-patterns)
- [FIFO Fairness](#fifo-fairness)
- [Cancel Interaction](#cancel-interaction)
- [Permit Leak Warning](#permit-leak-warning)
- [Lifecycle (No Explicit Destroy)](#lifecycle-no-explicit-destroy)
- [Scheduler Integration](#scheduler-integration)

## Overview / Motivation

Semaphore effects are useful when many tasks run concurrently and only a limited number should
enter a section at once.

- **Bounded concurrency**: allow at most `N` concurrent workers.
- **Mutual exclusion**: use one permit (`N = 1`) as a mutex.
- **Resource pooling**: model fixed-size pools (DB connections, API slots, GPU workers).

Because semaphores are effects, waiting is cooperative: blocked tasks yield to the scheduler and
other runnable tasks continue.

## Effect Definitions

Import surface:

```python
from doeff import AcquireSemaphore, CreateSemaphore, ReleaseSemaphore, Semaphore
```

### `CreateSemaphore(permits: int)`

Creates and returns a `Semaphore` handle with `permits` available permits.

- `permits` must be an integer `>= 1`.
- `CreateSemaphore(0)` raises `ValueError`.

```python
from doeff import CreateSemaphore, do

@do
def setup():
    sem = yield CreateSemaphore(3)
    return sem
```

### `AcquireSemaphore(sem)`

Acquires one permit from `sem`.

- If a permit is available and there are no earlier waiters, acquisition continues immediately.
- If no permit is available, the task is cooperatively parked and resumed later.

```python
from doeff import AcquireSemaphore, do

@do
def enter(sem):
    yield AcquireSemaphore(sem)
```

### `ReleaseSemaphore(sem)`

Releases one permit back to `sem`.

- If waiters exist, the released permit is handed to the oldest waiter (FIFO).
- Releasing more times than acquired raises `RuntimeError`.

```python
from doeff import ReleaseSemaphore, do

@do
def leave(sem):
    yield ReleaseSemaphore(sem)
```

## Usage Patterns

### Mutex: `Semaphore(1)` for exclusive access

```python
from doeff import AcquireSemaphore, CreateSemaphore, ReleaseSemaphore, do

@do
def with_mutex(sem, update):
    yield AcquireSemaphore(sem)
    try:
        return (yield update())
    finally:
        yield ReleaseSemaphore(sem)

@do
def program():
    sem = yield CreateSemaphore(1)
    return (yield with_mutex(sem, critical_update))
```

Use `try/finally` so permits are released even when work fails.

### Connection pool: `Semaphore(N)` for bounded parallelism

```python
from doeff import AcquireSemaphore, CreateSemaphore, Gather, ReleaseSemaphore, Spawn, Wait, do

@do
def pooled_call(sem, call):
    yield AcquireSemaphore(sem)
    try:
        return (yield call())
    finally:
        yield ReleaseSemaphore(sem)

@do
def run_batch(calls):
    sem = yield CreateSemaphore(8)  # at most 8 concurrent calls
    tasks = [yield Spawn(pooled_call(sem, call)) for call in calls]
    return (yield Gather(*[Wait(t) for t in tasks]))
```

## FIFO Fairness

Semaphore waiters are served in FIFO order.

- A task that waits earlier is resumed earlier.
- New acquirers do not bypass queued waiters.
- This prevents starvation under sustained contention.

## Cancel Interaction

If a task is blocked in `AcquireSemaphore(sem)` and that task is cancelled:

- The scheduler removes that task from the semaphore wait queue.
- The task resumes with `TaskCancelledError`.
- No permit is consumed by the cancelled task.

This keeps permit accounting correct even when cancellation races with contention.

## Permit Leak Warning

Semaphores do not track permit ownership. If a task acquires a permit and then fails or is
cancelled before `ReleaseSemaphore`, that permit is leaked.

- Leaked permits reduce effective capacity and can stall future acquirers.
- Always guard critical sections with `try/finally` so `ReleaseSemaphore` runs on both success
  and failure/cancellation paths.

## Lifecycle (No Explicit Destroy)

There is no explicit semaphore destroy API. Semaphore state is dropped when the handle is no
longer referenced and garbage collection reclaims it.

## Scheduler Integration

Semaphore effects are integrated with the scheduler's cooperative suspension model.

1. `AcquireSemaphore` tries to take a permit.
2. If unavailable, the scheduler parks the task and records it in the semaphore wait queue.
3. The parked task yields execution; other runnable tasks continue.
4. `ReleaseSemaphore` resumes the next waiter in FIFO order (or returns a permit to the semaphore
   if no waiter exists).

This design provides backpressure and fairness without thread blocking or busy waiting.
