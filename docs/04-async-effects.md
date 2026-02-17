# Async Effects

This chapter covers async integration and scheduler primitives for cooperative concurrency.

## Table of Contents

- [Runner and Handler Presets](#runner-and-handler-presets)
- [Await Effect](#await-effect)
- [Scheduler Effect Catalog](#scheduler-effect-catalog)
- [Waitables and Handles](#waitables-and-handles)
- [Task Lifecycle and Scheduling Model](#task-lifecycle-and-scheduling-model)
- [Gather Fail-Fast Semantics](#gather-fail-fast-semantics)
- [Promise Synchronization Patterns](#promise-synchronization-patterns)
- [Race Semantics](#race-semantics)
- [Common Mistakes](#common-mistakes)
- [Quick Reference](#quick-reference)

## Runner and Handler Presets

Pair each runner with the matching handler preset.

| Runner | Preset | Await Handler | Intended Context |
| --- | --- | --- | --- |
| `run(...)` | `default_handlers()` | `sync_await_handler` | Sync entrypoint |
| `await async_run(...)` | `default_async_handlers()` | `async_await_handler` | Async entrypoint / caller event loop |

```python
from doeff import async_run, default_async_handlers, default_handlers, run

sync_result = run(program(), handlers=default_handlers())
async_result = await async_run(program(), handlers=default_async_handlers())
```

## Await Effect

`Await(awaitable)` bridges Python awaitables (coroutines, tasks, futures) into a doeff program.

```python
import asyncio
from doeff import Await, do

@do
def fetch_value():
    value = yield Await(asyncio.sleep(0.1, result=42))
    return value
```

### Handler behavior

Both Await handlers bridge completion through `CreateExternalPromise` plus `Wait`:

- `sync_await_handler` (`run` preset):
  - submits the awaitable to a background asyncio loop thread
  - waits on `promise.future` via `Wait`
- `async_await_handler` (`async_run` preset):
  - kicks off awaitable submission through async runtime path
  - waits on `promise.future` via `Wait`

## Scheduler Effect Catalog

The scheduler primitives are:

| Effect | Input | Output | Purpose |
| --- | --- | --- | --- |
| `Spawn(program)` | doeff program | `Task[T]` | Start child task and continue immediately |
| `Wait(task_or_future)` | `Task` or `Future` | `T` | Suspend until one waitable resolves |
| `Gather(*waitables)` | waitables/programs/effects | `list[T]` | Suspend until all complete (input order) |
| `Race(*waitables)` | waitables | winner + value | Suspend until first completion |
| `CreatePromise()` | none | `Promise[T]` | Allocate doeff-internal promise |
| `CompletePromise(p, value)` | `Promise[T]`, `T` | `None` | Resolve promise successfully |
| `FailPromise(p, error)` | `Promise[Any]`, exception | `None` | Resolve promise with error |
| `CreateExternalPromise()` | none | `ExternalPromise[T]` | Allocate externally-completable promise |

## Waitables and Handles

- `Spawn(...)` returns a `Task[T]` handle.
- `Promise[T].future` is a `Future[T]` waitable.
- `ExternalPromise[T].future` is also a `Future[T]` waitable.
- `Wait`, `Gather`, and `Race` consume these waitable handles.

```python
from doeff import CreatePromise, Spawn, Wait, do

@do
def child():
    return 7

@do
def parent():
    task = yield Spawn(child())
    promise = yield CreatePromise()

    task_value = yield Wait(task)
    # promise_value = yield Wait(promise.future)
    return task_value
```

## Task Lifecycle and Scheduling Model

Scheduler state transitions are internal to the scheduler handler (`Pending`, `Running`, `Suspended`, `Blocked`, `Completed`, `Failed`, `Cancelled`).

At VM level, scheduling stays handler-internal:

- Wait/Race/Gather may park the current task if inputs are pending.
- Scheduler selects another ready task and continues execution.
- VM keeps stepping `Continue` states; task switching is not exposed as a VM primitive.
- When a waitable completes, scheduler callbacks wake blocked tasks and reschedule them.

This separation keeps scheduling logic in the scheduler handler while preserving a simple VM step model.

## Gather Fail-Fast Semantics

`Gather` is fail-fast:

- if any gathered waitable fails, `Gather` raises immediately
- if any gathered task is cancelled, `Gather` raises `TaskCancelledError`
- remaining tasks are not auto-cancelled

Use `Safe(...)` when you need partial results instead of fail-fast behavior.

```python
from doeff import Gather, Safe, Spawn, do

@do
def collect_all_even_on_errors():
    t1 = yield Spawn(Safe(work_1()))
    t2 = yield Spawn(Safe(work_2()))
    return (yield Gather(t1, t2))  # [Ok(...)/Err(...), Ok(...)/Err(...)]
```

## Promise Synchronization Patterns

### Internal promise (doeff-to-doeff)

Use `CreatePromise`, `CompletePromise`, and `FailPromise` when completion happens inside doeff.

```python
from doeff import CompletePromise, CreatePromise, Spawn, Wait, do

@do
def producer(p):
    yield CompletePromise(p, "ready")

@do
def consumer():
    p = yield CreatePromise()
    _ = yield Spawn(producer(p))
    return (yield Wait(p.future))
```

### External promise bridge (asyncio/threads/processes)

Use `CreateExternalPromise` when completion comes from outside the scheduler.

```python
import threading
from doeff import CreateExternalPromise, Wait, do

@do
def wait_for_external():
    promise = yield CreateExternalPromise()

    def worker():
        try:
            promise.complete("ok")
        except Exception as exc:
            promise.fail(exc)

    threading.Thread(target=worker, daemon=True).start()
    return (yield Wait(promise.future))
```

## Race Semantics

`Race(*waitables)` resumes on the first completed input.

- first completion wins
- if the winner fails, `Race` raises that error
- if the winner is cancelled, `Race` raises `TaskCancelledError`
- non-winning waitables continue unless you cancel them

Conceptually, race semantics are `(winner_index, value)` based on argument order.
If you need index-style handling, derive it from your input tuple and the winner handle.

```python
from doeff import Race, Spawn, do

@do
def first_result():
    t1 = yield Spawn(job("a", 0.3))
    t2 = yield Spawn(job("b", 0.1))
    waitables = (t1, t2)

    race_result = yield Race(*waitables)
    winner_index = waitables.index(race_result.first)
    return winner_index, race_result.value
```

## Common Mistakes

1. Passing `default_handlers()` to `async_run`.
Use `default_async_handlers()` for async entrypoints.

2. Passing a raw coroutine to `Wait(...)`.
Use `Await(coroutine)` for Python async work; use `Wait` for scheduler waitables.

3. Expecting `Gather` to keep going after first failure.
Use `Safe(...)` around child programs if you need partial success collection.

## Quick Reference

| Effect | Use when | Notes |
| --- | --- | --- |
| `Await(awaitable)` | waiting on Python async objects | Bridge from Python async into doeff |
| `Spawn(program)` | starting concurrent doeff task | Returns `Task[T]` |
| `Wait(waitable)` | waiting for one task/promise | Returns resolved value or raises |
| `Gather(*waitables)` | waiting for all children | Fail-fast on first error/cancellation |
| `Race(*waitables)` | waiting for first child | Winner determines result/error |
| `CreatePromise()` | internal producer/consumer sync | Complete via effects |
| `CompletePromise(...)` | resolve internal promise | Wakes waiters |
| `FailPromise(...)` | fail internal promise | Wakes waiters with error |
| `CreateExternalPromise()` | external callback/thread/process completion | Complete via `promise.complete()`/`promise.fail()` |
