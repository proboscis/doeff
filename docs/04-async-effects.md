# Async Effects

This chapter covers async integration and scheduler primitives for cooperative concurrency.

[![A narrated video, about two minutes: Spawn, Wait, Gather, Race and Cancel as effects handled by the scheduler, one more handler layer; tasks parking their k on a virtual clock and Gather returning values in input order; one task running at a time until it waits; Gather failing fast and Race leaving the loser running; and Cancel delivered into the task as TaskCancelledError, with its finally block running before the waiter hears](assets/videos/async-effects.gif)](assets/videos/async-effects.mp4)

## Table of Contents

- [Runner and Scheduled Programs](#runner-and-scheduled-programs)
- [Await Effect](#await-effect)
- [Scheduler Effect Catalog](#scheduler-effect-catalog)
- [Waitables and Handles](#waitables-and-handles)
- [Task Lifecycle and Scheduling Model](#task-lifecycle-and-scheduling-model)
- [Gather Fail-Fast Semantics](#gather-fail-fast-semantics)
- [Race Semantics](#race-semantics)
- [Cancel and TaskCancelledError](#cancel-and-taskcancellederror)
- [Task Exceptions Nothing Retrieved](#task-exceptions-nothing-retrieved)
- [Promise vs ExternalPromise](#promise-vs-externalpromise)
- [Common Mistakes](#common-mistakes)
- [Quick Reference](#quick-reference)

## Runner and Scheduled Programs

There is a single runner: `run(doexpr)`. It takes one argument (a program expression) and returns the
raw result value directly. To enable scheduler effects (Spawn, Wait, Gather, Race, Cancel), wrap
your program with `scheduled()`.

For custom handler composition, call a Program -> Program handler installer directly, for example
`handler(program)`.

```python
from doeff import Gather, Spawn, do, run
from doeff_core_effects import scheduled

@do
def square(n):
    return n * n

@do
def program():
    tasks = []
    for n in (1, 2, 3):
        tasks.append((yield Spawn(square(n))))
    return (yield Gather(*tasks))

result = run(scheduled(program()))
print(result)  # [1, 4, 9]
```

Handler composition example:

```python
from doeff import Ask, Tell, do, run
from doeff_core_effects import reader, state, writer
from doeff_core_effects import scheduled

@do
def my_program():
    name = yield Ask("name")
    yield Tell(f"hello {name}")
    return name.upper()

prog = my_program()
prog = writer(prog)
prog = state()(prog)
prog = reader(env={"name": "doeff"})(prog)
result = run(scheduled(prog))
print(result)  # DOEFF
```

## Await Effect

`Await(awaitable)` bridges Python awaitables (coroutines, tasks, futures) into a doeff program.

```python
from doeff import Await, do, run
from doeff_core_effects import await_handler
from doeff_core_effects import scheduled

async def read_answer() -> int:
    return 42

@do
def fetch_value():
    value = yield Await(read_answer())
    return value

print(run(scheduled(await_handler()(fetch_value()))))  # 42
```

`@do` does not take an `async def` function: it raises `TypeError` where it is applied, since
calling the function would give a coroutine that nothing awaits. Write a plain `def` and each
`await coro` as `value = yield Await(coro)`, as `fetch_value` does above.

To wait for a duration, yield doeff-time's `Delay(seconds)` rather than
`Await(asyncio.sleep(seconds))`: the time handler then owns the clock, and a test can run the
program on `sim_time_handler()`'s virtual clock.

### Handler behavior

`Await` is handled by `await_handler()` (from `doeff_core_effects._handlers`), installed inside
`scheduled()`. It bridges completion through `CreateExternalPromise` plus `Wait`:

- it submits the awaitable to one background asyncio loop thread, shared by the process
- it waits on `promise.future` via `Wait`, so other tasks run meanwhile
- spawned Await work overlaps: two spawned tasks that each await `asyncio.sleep(0.5)` finish
  together in about 0.5 seconds

## Scheduler Effect Catalog

The scheduler primitives are:

| Effect | Input | Output | Purpose |
| --- | --- | --- | --- |
| `Spawn(program)` | doeff program | `Task[T]` | Start child task and continue immediately |
| `Wait(task_or_future)` | `Task` or `Future` waitable handle | `T` | Suspend until one waitable resolves |
| `Gather(*waitables)` | `Task`/`Future` waitable handles | `list[T]` | Suspend until all complete (input order) |
| `Race(*waitables)` | `Task`/`Future` waitable handles | `T` | Suspend until first completion |
| `Cancel(task)` | `Task[T]` | `None` | Request task cancellation (effect, not method) |
| `SchedulerYield` | internal | internal | Cooperative preemption point inserted per yield |
| `CreatePromise()` | none | `Promise[T]` | Allocate doeff-internal promise |
| `CompletePromise(p, value, *, yield_to_woken=True)` | `Promise[T]`, `T` | `None` | Resolve promise successfully; with `yield_to_woken=False` the completer keeps its turn |
| `FailPromise(p, error)` | `Promise[Any]`, exception | `None` | Resolve promise with error |
| `CreateExternalPromise()` | none | `ExternalPromise[T]` | Allocate externally-completable promise |

`Race` returns the winning value directly. Keep the handles you passed to it to cancel the
losers (cancelling a task that already completed is a no-op).

## Waitables and Handles

- `Spawn(...)` returns a `Task[T]` handle.
- `Promise[T].future` is a `Future[T]` waitable.
- `ExternalPromise[T].future` is also a `Future[T]` waitable.
- `Wait`, `Gather`, and `Race` consume these waitable handles.
- Raw programs are not waitables. Spawn programs first, then wait on the returned handles.

```python
from doeff import CompletePromise, CreatePromise, Spawn, Wait, do, run
from doeff_core_effects import scheduled

@do
def child():
    return 7

@do
def parent():
    task = yield Spawn(child())
    promise = yield CreatePromise()
    yield CompletePromise(promise, 8)

    task_value = yield Wait(task)
    promise_value = yield Wait(promise.future)
    return task_value, promise_value

print(run(scheduled(parent())))  # (7, 8)
```

```python
from doeff import Gather, Race, Spawn, do, run
from doeff_core_effects import scheduled

@do
def work_1():
    return "one"

@do
def work_2():
    return "two"

@do
def parallel():
    task1 = yield Spawn(work_1())
    task2 = yield Spawn(work_2())

    winner = yield Race(task1, task2)
    all_results = yield Gather(task1, task2)
    return winner, all_results

print(run(scheduled(parallel())))  # ('one', ['one', 'two'])
```

## Task Lifecycle and Scheduling Model

Scheduler state transitions are internal to the scheduler handler
(`Pending`, `Running`, `Suspended`, `Blocked`, `Completed`, `Failed`, `Cancelled`).

At VM level, scheduling stays handler-internal:

- Wait/Race/Gather may park the current task if inputs are pending.
- Scheduler selects another ready task and continues execution.
- VM keeps stepping `Continue` states; task switching is not exposed as a VM primitive.
- When a waitable completes, scheduler callbacks wake blocked tasks and reschedule them.

The scheduler is single-threaded with no OS-style preemption. Ready tasks are interleaved in
round-robin order, and context switches happen at effect yield points via internal
`SchedulerYield` dispatch.

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

Use `Try(...)` when you need partial results instead of fail-fast behavior.

```python
from doeff import Gather, Try, Spawn, do, run
from doeff_core_effects import try_handler
from doeff_core_effects import scheduled

@do
def work_1():
    return "one"

@do
def work_2():
    raise ValueError("boom")

@do
def collect_all_even_on_errors():
    t1 = yield Spawn(Try(work_1()))
    t2 = yield Spawn(Try(work_2()))
    return (yield Gather(t1, t2))  # [Ok(...)/Err(...), Ok(...)/Err(...)]

print(run(scheduled(try_handler(collect_all_even_on_errors()))))  # [Ok('one'), Err(ValueError('boom'))]
```

## Race Semantics

`Race(*waitables)` resumes on first completion of a `Task`/`Future` waitable:

- the first completed waitable determines the result: `Race` returns its value directly
- if that completion is an error, `Race` raises that error
- if that completion is cancellation, `Race` raises `TaskCancelledError`

Race losers continue running by default. Cancel them explicitly if needed (for Task inputs):
`for t in (t1, t2): yield Cancel(t)` — cancelling the task that already completed is a no-op.

The jobs below wait with doeff-time's `Delay`, and the run puts `sim_time_handler()` around them, so
they wait on a virtual clock: the run takes no real time and `b` wins on every run. `doeff-time`
(import name `doeff_time`) is not installed by `pip install doeff`; install it from PyPI with
`pip install doeff-time`.

```python
from doeff import Cancel, Race, Spawn, Try, Wait, do, run
from doeff_core_effects import try_handler
from doeff_core_effects import scheduled
from doeff_time import Delay, sim_time_handler

@do
def job(name, seconds):
    yield Delay(seconds)
    return name

@do
def first_result():
    t1 = yield Spawn(job("a", 0.3))
    t2 = yield Spawn(job("b", 0.1))
    value = yield Race(t1, t2)

    # Race losers continue by default; cancel explicitly for teardown,
    # then wait until the cancelled loser has finished unwinding.
    for t in (t1, t2):
        yield Cancel(t)
    for t in (t1, t2):
        yield Try(Wait(t))
    return value

print(run(scheduled(sim_time_handler()(try_handler(first_result())))))  # b
```

## Cancel and TaskCancelledError

Cancellation is explicit and delivered into the task:

- request cancellation via `yield Cancel(task)`; the request returns immediately
- a task that never started is cancelled without running any of its body
- a started task receives `TaskCancelledError` at the point where it is suspended (`Wait`,
  `Gather`, `Race`, `AcquireSemaphore`, `Await`, or a queued resume), so its `except` and
  `finally` blocks run; a task that cancels itself receives it at its own `Cancel`
- cleanup may perform effects (scheduler effects, `Await` I/O, user effects); the task stays
  live until it has finished unwinding
- waiters (`Wait`, `Gather`, `Race`) are woken only after the task has finished unwinding, and
  observe it as `TaskCancelledError`; to be sure the cleanup ran, `Wait` on the task after
  `Cancel` (work still unwinding when the root body returns is abandoned, with the #501 warning)
- if the cleanup raises a different exception, waiters observe that exception instead
- a task that catches `TaskCancelledError` and carries on keeps running (one `Cancel` delivers
  the exception once); waiters still observe `TaskCancelledError` when it ends, and its return
  value is discarded. Another `Cancel` delivers the exception again at its next suspension point.
  `TaskCancelledError` is an `Exception` subclass, so a broad `except Exception` also catches it
- cancelling `Completed`/`Failed`/`Cancelled` tasks is a no-op

```python
from doeff import Cancel, CreatePromise, Ok, Err, Try, Spawn, Wait, do, run
from doeff_core_effects import slog
from doeff_core_effects import slog_handler, try_handler
from doeff_core_effects import scheduled

@do
def acquire_lease():
    return "lease-1"

@do
def run_remote(lease):
    reply = yield CreatePromise()  # stands for a reply that has not arrived yet
    return (yield Wait(reply.future))

@do
def release_lease(lease):
    yield slog("released", lease=lease)  # slog_handler prints on stderr:     INFO released lease=lease-1

@do
def work():
    lease = yield acquire_lease()
    try:
        return (yield run_remote(lease))
    finally:
        yield release_lease(lease)  # runs on Cancel, may perform effects

@do
def cancel_child():
    task = yield Spawn(work())
    yield Cancel(task)
    result = yield Try(Wait(task))
    match result:
        case Err(error=e):
            return e  # TaskCancelledError, after work()'s finally ran
        case Ok(value=v):
            return v

print(repr(run(scheduled(slog_handler(try_handler(cancel_child()))))))  # TaskCancelledError()
```

### Cancelling a task parked on external work

When a cancelled task was parked on an `ExternalPromise` (directly via `Wait`/`Gather`/`Race`,
or indirectly via `Await`), the cancellation reaches the external producer:

- if no live (non-cancelled) waiter remains on that pending promise, the scheduler marks it
  cancelled — later `complete()`/`fail()` calls are ignored and later waits raise
  `TaskCancelledError` — and runs the callbacks registered with `promise.on_cancel(callback)`
- `Await` registers the bridge future's `cancel`, so the awaited coroutine receives
  `asyncio.CancelledError` at its current `await` and its `finally` blocks run
- `Cancel` only requests this; it does not wait for the coroutine to finish unwinding, and a
  coroutine that swallows `CancelledError` keeps running (its result is discarded)
- a promise without `on_cancel` callbacks keeps the plain behaviour (stays pending)
- a failing callback is re-raised to the `Cancel` caller as
  `ExternalPromiseCancelCallbackError` after the cancellation has taken effect

The program below asks for processes with a `RunProcess` effect and does no I/O itself. The
`subprocess_runner` handler starts the process, parks the task on an `ExternalPromise` that a
reader thread completes, and registers `proc.terminate` with `on_cancel`. When the fast process
wins the `Race`, cancelling the slow task terminates the slow process:

```python
import subprocess
import sys
import threading
from dataclasses import dataclass

from doeff import (
    Cancel, CreateExternalPromise, EffectBase, Race, Resume, Spawn, Try, Wait, do, handler, run,
)
from doeff_core_effects import try_handler
from doeff_core_effects import scheduled

@dataclass(frozen=True)
class RunProcess(EffectBase):  # an effect: "run this command, give its output"
    args: tuple[str, ...]

def run_python(code):
    return RunProcess((sys.executable, "-c", code))

def read_output(proc, ep):
    ep.complete(proc.communicate()[0].strip())  # runs on the reader thread

started = []

@handler
def subprocess_runner(effect: RunProcess, k):
    proc = subprocess.Popen(effect.args, stdout=subprocess.PIPE, text=True)
    started.append(proc)
    ep = yield CreateExternalPromise()
    ep.on_cancel(proc.terminate)  # runs on the scheduler thread; keep it quick
    threading.Thread(target=read_output, args=(proc, ep), daemon=True).start()
    output = yield Wait(ep.future)
    return (yield Resume(k, output))

@do
def first_output():
    slow = yield Spawn(run_python("import time; time.sleep(30)"))
    fast = yield Spawn(run_python("print('fast')"))
    winner = yield Race(slow, fast)
    yield Cancel(slow)  # the handler's on_cancel terminates the slow process
    yield Try(Wait(slow))
    return winner

print(run(scheduled(try_handler(subprocess_runner(first_output())))))  # fast
print(sorted(proc.wait() for proc in started))  # [-15, 0]
```

In a test, a handler that answers `RunProcess` with made-up output takes the place of
`subprocess_runner`, and `first_output` runs without starting a process.

## Task Exceptions Nothing Retrieved

A spawned task's exception reaches whoever waits on the task. When nothing does, the run still
returns its value, and the scheduler warns at the end of the run — like asyncio's "Task exception
was never retrieved":

- a task's outcome is claimed once a `Wait`, `Gather` or `Race` is given the task, or a `Cancel` or
  `Discard` — before or after the task ends. A `Gather` sibling that fails after the fail-fast and
  a `Race` loser that fails later were given to the `Gather` / `Race`, so they are claimed
- a task that ended with an exception while nothing claimed its outcome is named in one
  `RuntimeWarning` when the run ends (on the root's error path too): the task, its program, where it
  was spawned, the exception, and where it was raised. `daemon=True` does not silence it — the run
  does not wait for a daemon task, but its exception is still lost
- a task that ended with `TaskCancelledError` ended by cancellation, so it is not named
- wrap the task's program in `Try(...)` when a failure is expected and its value is not needed

```python
import warnings

from doeff import Spawn, do, run
from doeff_core_effects import scheduled

@do
def send_report():
    raise ValueError("mail server down")

@do
def main():
    yield Spawn(send_report())  # nothing waits on the task
    return "done"

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    print(run(scheduled(main())))  # done
print(caught[0].category.__name__)  # RuntimeWarning
print(str(caught[0].message).split(":")[0])  # spawned task exception was never retrieved
print("running send_report" in str(caught[0].message))  # True
print("ValueError: mail server down" in str(caught[0].message))  # True
```

## Promise vs ExternalPromise

Use `Promise` when producer and consumer are both inside doeff. Use `ExternalPromise` when
external code (thread, callback, event-loop task, process) completes the value.

| Type | Created by | Completed by | Completion path |
| --- | --- | --- | --- |
| `Promise` | `CreatePromise()` | `CompletePromise` / `FailPromise` | Scheduler effect dispatch |
| `ExternalPromise` | `CreateExternalPromise()` | `promise.complete()` / `promise.fail()` | External queue drained by scheduler |

```python
from doeff import CompletePromise, CreatePromise, Spawn, Wait, do, run
from doeff_core_effects import scheduled

@do
def internal_sync():
    p = yield CreatePromise()
    _ = yield Spawn(complete_later(p))
    return (yield Wait(p.future))

@do
def complete_later(p):
    yield CompletePromise(p, "ready")

print(run(scheduled(internal_sync())))  # ready
```

## Common Mistakes

1. Passing a raw coroutine to `Wait(...)`.
Use `Await(coroutine)` for Python async work; use `Wait` for scheduler waitables.

2. Passing raw programs directly to `Wait`, `Gather`, or `Race`.
Use `task = yield Spawn(program)` first, then pass the returned `Task` (or a `Future`) handle.

3. Expecting `Gather` to keep going after first failure.
Use `Try(...)` around child programs if you need partial success collection.

4. Using `task.cancel()` instead of `yield Cancel(task)`.
`Cancel` is an effect that must be yielded; tasks do not have a `.cancel()` method.

5. Spawning a task and never waiting on it.
Its exception reaches nobody; the run ends with a "spawned task exception was never retrieved"
`RuntimeWarning`. `Wait` on the task, or `Cancel`/`Discard` it when its outcome is not needed.

6. Forgetting to wrap the program with `scheduled()`.
Scheduler effects (`Spawn`, `Wait`, `Gather`, `Race`, `Cancel`) require
`run(scheduled(prog))`.

## Quick Reference

| Effect | Use when | Notes |
| --- | --- | --- |
| `Await(awaitable)` | waiting on Python async objects | Bridge from Python async into doeff |
| `Spawn(program)` | starting concurrent doeff task | Returns `Task[T]` |
| `Wait(waitable)` | waiting for one task/future handle | Accepts only `Task` or `Future` handles |
| `Gather(*waitables)` | waiting for all spawned children | Pass `Task`/`Future` handles; fail-fast on first error/cancellation |
| `Race(*waitables)` | waiting for first spawned child | Returns the winning value; losers continue unless cancelled |
| `Cancel(task)` | requesting task cancellation | Effect; yield it. Applies to `Pending`/`Running`/`Suspended`/`Blocked`; terminal states are no-op |
| `SchedulerYield` | understanding scheduler fairness | Internal cooperative preemption point |
| `CreatePromise()` | internal producer/consumer sync | Complete/fail via effects |
| `CompletePromise(...)` | resolve internal promise | Wakes waiters |
| `FailPromise(...)` | fail internal promise | Wakes waiters with error |
| `CreateExternalPromise()` | external callback/thread/process completion | Complete via `promise.complete()`/`promise.fail()` |
