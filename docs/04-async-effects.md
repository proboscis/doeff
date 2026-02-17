# Async Effects

This chapter covers async integration and scheduler primitives for cooperative concurrency.

## Table of Contents

- [Runner and Handler Presets](#runner-and-handler-presets)
- [Await Effect](#await-effect)
- [Scheduler Effect Catalog](#scheduler-effect-catalog)
- [Waitables and Handles](#waitables-and-handles)
- [Task Lifecycle and Scheduling Model](#task-lifecycle-and-scheduling-model)
- [Gather Fail-Fast Semantics](#gather-fail-fast-semantics)
- [Race Semantics](#race-semantics)
- [Cancel and TaskCancelledError](#cancel-and-taskcancellederror)
- [Promise vs ExternalPromise](#promise-vs-externalpromise)
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
  - spawned Await work is sequential (no overlap)
- `async_await_handler` (`async_run` preset):
  - schedules awaitable submission through the async runtime path
  - waits on `promise.future` via `Wait`
  - spawned Await work can overlap

### Await timing note

`Await` on sync `run(...)` is not concurrent by itself. If you spawn two Await tasks, each
`Await` resolves sequentially under `sync_await_handler`. Concurrency requires
`async_await_handler` with `async_run(...)`.

## Scheduler Effect Catalog

The scheduler primitives are:

| Effect | Input | Output | Purpose |
| --- | --- | --- | --- |
| `Spawn(program)` | doeff program | `Task[T]` | Start child task and continue immediately |
| `Wait(task_or_future)` | `Task` or `Future` waitable handle | `T` | Suspend until one waitable resolves |
| `Gather(*waitables)` | `Task`/`Future` waitable handles | `list[T]` | Suspend until all complete (input order) |
| `Race(*waitables)` | `Task`/`Future` waitable handles | winner value | Suspend until first completion |
| `Cancel(task)` | `Task[T]` | `None` | Request task cancellation (`yield task.cancel()`) |
| `SchedulerYield` | internal | internal | Cooperative preemption point inserted per yield |
| `CreatePromise()` | none | `Promise[T]` | Allocate doeff-internal promise |
| `CompletePromise(p, value)` | `Promise[T]`, `T` | `None` | Resolve promise successfully |
| `FailPromise(p, error)` | `Promise[Any]`, exception | `None` | Resolve promise with error |
| `CreateExternalPromise()` | none | `ExternalPromise[T]` | Allocate externally-completable promise |

## Waitables and Handles

- `Spawn(...)` returns a `Task[T]` handle.
- `Promise[T].future` is a `Future[T]` waitable.
- `ExternalPromise[T].future` is also a `Future[T]` waitable.
- `Wait`, `Gather`, and `Race` consume these waitable handles.
- Raw programs are not waitables. Spawn programs first, then wait on the returned handles.

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
    promise_value = yield Wait(promise.future)
    return task_value, promise_value
```

```python
from doeff import Gather, Race, Spawn, do

@do
def parallel():
    task1 = yield Spawn(work_1())
    task2 = yield Spawn(work_2())

    winner = yield Race(task1, task2)
    all_results = yield Gather(task1, task2)
    return winner, all_results
```

## Task Lifecycle and Scheduling Model

Scheduler state transitions are internal to the scheduler handler
(`Pending`, `Running`, `Suspended`, `Blocked`, `Completed`, `Failed`, `Cancelled`).

At VM level, scheduling stays handler-internal:

- Wait/Race/Gather may park the current task if inputs are pending.
- Scheduler selects another ready task and continues execution.
- VM keeps stepping `Continue` states; task switching is not exposed as a VM primitive.
- When a waitable completes, scheduler callbacks wake blocked tasks and reschedule them.

### store isolation

Scheduler tasks run with store isolation:

- `state` and `log` are isolated per task (snapshot at spawn, switched on task context switch)
- `env` is shared across tasks and treated as read-only scheduler context

This means parent and child tasks do not share mutable state/log writes, but they do share
the same environment view.

### Preemption (`SchedulerYield`)

Preemption is cooperative. The scheduler inserts an internal `SchedulerYield` point after
each task yield, so every effect dispatch is a potential context switch point.

`SchedulerYield` is internal (not something user code should yield directly), but it explains
why long-running concurrent workloads must keep yielding effects to remain fair.

## Gather Fail-Fast Semantics

`Gather` is fail-fast for `Task`/`Future` waitable inputs:

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

## Race Semantics

`Race(*waitables)` resumes on first completion of a `Task`/`Future` waitable:

- first completed waitable determines result
- if that completion is an error, `Race` raises that error
- if that completion is cancellation, `Race` raises `TaskCancelledError`

For first-wins workflows, apply sibling cancellation immediately after `Race`.

```python
from doeff import Race, Spawn, do

@do
def first_result():
    t1 = yield Spawn(job("a", 0.3))
    t2 = yield Spawn(job("b", 0.1))
    value = yield Race(t1, t2)

    # sibling cancellation for branch teardown
    _ = yield t1.cancel()
    _ = yield t2.cancel()
    return value
```

## Cancel and TaskCancelledError

Cancellation is explicit and cooperative:

- request cancellation via `yield task.cancel()`
- waiters (`Wait`, `Gather`, `Race`) observe cancelled tasks as `TaskCancelledError`
- cancellation request returns immediately; error appears when the task is joined/observed

```python
from doeff import Safe, Spawn, Wait, do

@do
def cancel_child():
    task = yield Spawn(work())
    _ = yield task.cancel()
    return (yield Safe(Wait(task)))  # Err(TaskCancelledError)
```

## Promise vs ExternalPromise

Use `Promise` when producer and consumer are both inside doeff. Use `ExternalPromise` when
external code (thread, callback, event-loop task, process) completes the value.

| Type | Created by | Completed by | Completion path |
| --- | --- | --- | --- |
| `Promise` | `CreatePromise()` | `CompletePromise` / `FailPromise` | Scheduler effect dispatch |
| `ExternalPromise` | `CreateExternalPromise()` | `promise.complete()` / `promise.fail()` | External queue drained by scheduler |

```python
from doeff import CompletePromise, CreatePromise, Spawn, Wait, do

@do
def internal_sync():
    p = yield CreatePromise()
    _ = yield Spawn(complete_later(p))
    return (yield Wait(p.future))

@do
def complete_later(p):
    yield CompletePromise(p, "ready")
```

## Common Mistakes

1. Passing `default_handlers()` to `async_run`.
Use `default_async_handlers()` for async entrypoints.

2. Passing a raw coroutine to `Wait(...)`.
Use `Await(coroutine)` for Python async work; use `Wait` for scheduler waitables.

3. Passing raw programs directly to `Wait`, `Gather`, or `Race`.
Use `task = yield Spawn(program)` first, then pass the returned `Task` (or a `Future`) handle.

4. Expecting `Gather` to keep going after first failure.
Use `Safe(...)` around child programs if you need partial success collection.

5. Assuming `Await` is concurrent under `run(...)`.
It is sequential under `sync_await_handler`; use `async_run(...)` for overlap.

## Quick Reference

| Effect | Use when | Notes |
| --- | --- | --- |
| `Await(awaitable)` | waiting on Python async objects | Bridge from Python async into doeff |
| `Spawn(program)` | starting concurrent doeff task | Returns `Task[T]` |
| `Wait(waitable)` | waiting for one task/future handle | Accepts only `Task` or `Future` handles |
| `Gather(*waitables)` | waiting for all spawned children | Pass `Task`/`Future` handles; fail-fast on first error/cancellation |
| `Race(*waitables)` | waiting for first spawned child | Pass `Task`/`Future` handles; first completion determines result |
| `Cancel(task)` | requesting task cancellation | User API is `yield task.cancel()` |
| `SchedulerYield` | understanding scheduler fairness | Internal cooperative preemption point |
| `CreatePromise()` | internal producer/consumer sync | Complete/fail via effects |
| `CompletePromise(...)` | resolve internal promise | Wakes waiters |
| `FailPromise(...)` | fail internal promise | Wakes waiters with error |
| `CreateExternalPromise()` | external callback/thread/process completion | Complete via `promise.complete()`/`promise.fail()` |
