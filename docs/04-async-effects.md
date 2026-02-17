# Async Effects

This chapter explains async integration in doeff and the scheduler effects used for concurrency.

## Table of Contents

- [Runner and Handler Presets](#runner-and-handler-presets)
- [Await Effect](#await-effect)
- [Await vs Wait](#await-vs-wait)
- [Spawn, Gather, and Wait](#spawn-gather-and-wait)
- [Concurrency Under Spawn](#concurrency-under-spawn)
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

`Await(awaitable)` is the bridge for Python asyncio awaitables inside a doeff program.

```python
import asyncio
from doeff import Await, do

@do
def fetch_value():
    value = yield Await(asyncio.sleep(0.1, result=42))
    return value
```

### Handler behavior

Both Await handlers bridge completion through `CreateExternalPromise` + `Wait`.

- `sync_await_handler` (`run` preset):
  - Submits the awaitable to the sync bridge (background asyncio loop thread).
  - Waits for the promise future, then resumes continuation.
- `async_await_handler` (`async_run` preset):
  - Yields `PythonAsyncSyntaxEscape` so kickoff runs through the async driver path.
  - Waits for the promise future, then resumes continuation.

### Wrong pairing behavior

- `run(..., handlers=default_async_handlers())` fails with a `TypeError` (`CallAsync requires async_run...`).
- `async_run(..., handlers=default_handlers())` runs, but uses sync Await semantics rather than the async preset.

## Await vs Wait

Use `Await` for Python awaitables and `Wait` for doeff scheduler handles.

| Use `Await` | Use `Wait` |
| --- | --- |
| Python coroutines (`async def`) | doeff `Task` / `Future` handles |
| `asyncio.sleep(...)` | values returned by `Spawn(...)` |
| async libraries (`aiohttp`, `httpx`, etc.) | `ExternalPromise.future` |

### Await example

```python
import asyncio
from doeff import Await, do

@do
def get_payload():
    return (yield Await(asyncio.sleep(0.01, result={"ok": True})))
```

### Wait example

```python
import asyncio
from doeff import Await, Spawn, Wait, do

@do
def child():
    return (yield Await(asyncio.sleep(0.01, result="done")))

@do
def parent():
    task = yield Spawn(child())
    return (yield Wait(task))
```

## Spawn, Gather, and Wait

`Spawn`, `Gather`, and `Wait` are scheduler primitives for doeff tasks.

- `Spawn(program)`: run a doeff program as a background task and return a `Task` handle.
- `Gather(*items)`: wait for multiple waitables/programs and return values.
- `Wait(task_or_future)`: wait for one spawned task/future.

```python
import asyncio
from doeff import Await, Gather, Spawn, Wait, do

@do
def worker(name: str, delay: float):
    return (yield Await(asyncio.sleep(delay, result=name)))

@do
def orchestrate():
    t1 = yield Spawn(worker("a", 0.05))
    t2 = yield Spawn(worker("b", 0.05))

    both = yield Gather(t1, t2)
    one = yield Wait(t1)
    return both, one
```

## Concurrency Under Spawn

The right way to reason about Spawn concurrency is to benchmark your selected runner+preset pair.

```python
import asyncio
import time
from doeff import Await, Gather, Spawn, async_run, default_async_handlers, default_handlers, do, run

@do
def child(label: str):
    return (yield Await(asyncio.sleep(0.1, result=label)))

@do
def parent():
    t1 = yield Spawn(child("left"))
    t2 = yield Spawn(child("right"))
    return tuple((yield Gather(t1, t2)))

sync_start = time.monotonic()
sync_result = run(parent(), handlers=default_handlers())
print("sync", sync_result.value, time.monotonic() - sync_start)

async def main():
    async_start = time.monotonic()
    async_result = await async_run(parent(), handlers=default_async_handlers())
    print("async", async_result.value, time.monotonic() - async_start)

asyncio.run(main())
```

In current runtime behavior, both preset pairs should return the same values and typically complete near one sleep interval for this pattern.

## Common Mistakes

1. Passing `default_handlers()` to `async_run` by habit.
Use `default_async_handlers()` for the async entrypoint.

2. Passing a coroutine to `Wait(...)`.
`Wait` expects a doeff scheduler handle (`Task`/`Future`), not a Python coroutine.

## Quick Reference

| Effect | Purpose | Input | Output |
| --- | --- | --- | --- |
| `Await(awaitable)` | Bridge Python async awaitable | Python awaitable/coroutine | Awaited value |
| `Spawn(program)` | Start background doeff task | doeff program/effect | `Task` handle |
| `Gather(*items)` | Wait for many items | Waitables/programs/effects | List of values |
| `Wait(waitable)` | Wait for one item | `Task` or `Future` handle | Value |

Use these with explicit handler presets so async behavior is intentional and predictable.
