# SPEC-EFF-011: Await Effect (asyncio Bridge)

## Status: Draft | **Ref:** ISSUE-CORE-495

## Summary

The `Await` effect bridges Python asyncio coroutines into doeff programs.
It is the **only** mechanism for running Python `async def` functions
within the doeff effect system. It is NOT for doeff-native futures — use
`Wait` (SPEC-SCHED-001) for those.

## Motivation

doeff programs are generator-based (`@do` / `yield`), not asyncio-based.
When a doeff program needs to call an asyncio library (aiohttp, httpx,
asyncio.sleep, etc.), it must bridge between the two worlds. `Await` is
that bridge.

## Effect Definition

```rust
#[pyclass(extends=PyEffectBase, frozen)]
pub struct PyAwaitEffect {
    #[pyo3(get)]
    pub awaitable: PyObject,  // Python Awaitable / Coroutine
}
```

Python-side:

```python
@dataclass(frozen=True)
class PythonAsyncioAwaitEffect(EffectBase):
    awaitable: Awaitable[Any]
```

Usage: `result = yield Await(some_coroutine())`

## Semantics

1. The handler receives the `Await` effect via normal handler dispatch
2. The handler bridges the awaitable to external execution and waits for
   completion via ExternalPromise + Wait
3. When the awaitable completes, the result is resumed into the continuation
4. If the awaitable raises, the exception is thrown into the continuation

`Await` is a **blocking** operation from the doeff program's perspective —
the generator is suspended until the coroutine completes.

## Architectural Invariants

### Handlers are user-space entities

All handlers — including those written in Rust for performance — are
**user-space** code. They are NOT part of the VM. The VM is a stepping
engine that dispatches effects to handlers; it has no knowledge of specific
effects like Await.

The Await effect is a `Effect::Python` from the VM's perspective. The VM
dispatches it to whatever handler is in the handler stack. The VM never
directly interprets or short-circuits Await handling.

### Handler immutability

`run()` and `async_run()` must NEVER modify the passed handlers. No
swapping, no normalization, no detection-based branching. The user is
responsible for passing the correct handler set for their execution context.

### Two handler presets

Two `default_handlers` functions are provided. The user picks based on
which run function they use:

```python
run(prog, handlers=default_handlers())              # sync preset
await async_run(prog, handlers=default_async_handlers())  # async preset
```

Wrong combination behavior:
- `async_await_handler` under `run()` → `TypeError` (sync driver cannot
  handle `CallAsync` / `PythonAsyncSyntaxEscape`)
- `sync_await_handler` under `async_run()` → works but blocks (no
  concurrency for spawned Await tasks); this is the user's choice

## Handler Specifications

### sync_await_handler (for `run()`)

Handles `PythonAsyncioAwaitEffect` by running the awaitable on a dedicated
background thread with its own asyncio event loop, bridging completion back
via ExternalPromise and Wait.

```
PythonAsyncioAwaitEffect
  │
  ▼
CreateExternalPromise → promise
  │
  ▼
Submit awaitable to background thread (own asyncio loop)
  │  asyncio.run_coroutine_threadsafe(awaitable, background_loop)
  │  on completion → promise.complete(result)
  │  on error     → promise.fail(exc)
  │
  ▼
Wait(promise.future)  → scheduler parks task until promise completes
  │
  ▼
Resume(k, value)
```

Reference implementation:

```python
def sync_await_handler(effect, k):
    if isinstance(effect, PythonAsyncioAwaitEffect):
        promise = yield CreateExternalPromise()
        _submit_awaitable(effect.awaitable, promise)  # background loop
        values = yield gather(promise.future)
        return (yield Resume(k, values[0]))
    yield Pass()
```

| Property | Value |
|----------|-------|
| Concurrency model | Blocking — awaitable runs on background thread |
| Event loop | Dedicated background loop (not caller's) |
| Spawned task overlap | No — scheduler blocks on Wait until complete |
| Use with | `run()` |

### async_await_handler (for `async_run()`)

Handles `PythonAsyncioAwaitEffect` by yielding `PythonAsyncSyntaxEscape`
to schedule the awaitable as an `asyncio.Task` on the **caller's event
loop**, then waiting for completion via ExternalPromise and Wait.

```
PythonAsyncioAwaitEffect
  │
  ▼
CreateExternalPromise → promise
  │
  ▼
PythonAsyncSyntaxEscape(action=create_task_action)
  │  action creates asyncio.Task on caller's event loop:
  │    async def fire():
  │        try:
  │            result = await awaitable
  │            promise.complete(result)
  │        except BaseException as exc:
  │            promise.fail(exc)
  │    asyncio.create_task(fire())
  │
  ▼
Wait(promise.future)  → scheduler parks task, advances to next task
  │
  ▼
Resume(k, value)
```

Reference implementation:

```python
def async_await_handler(effect, k):
    if isinstance(effect, PythonAsyncioAwaitEffect):
        promise = yield CreateExternalPromise()

        async def _fire():
            try:
                result = await effect.awaitable
                promise.complete(result)
            except BaseException as exc:
                promise.fail(exc)

        _ = yield PythonAsyncSyntaxEscape(
            action=lambda: asyncio.create_task(_fire())
        )
        values = yield gather(promise.future)
        return (yield Resume(k, values[0]))
    yield Pass()
```

| Property | Value |
|----------|-------|
| Concurrency model | Non-blocking — awaitable runs on caller's event loop |
| Event loop | Caller's event loop (via `asyncio.create_task`) |
| Spawned task overlap | Yes — scheduler parks and advances to other tasks |
| Use with | `async_run()` |

### Key difference: concurrency under Spawn

```python
@do
def parent():
    t1 = yield Spawn(Await(asyncio.sleep(0.1, result="a")))
    t2 = yield Spawn(Await(asyncio.sleep(0.1, result="b")))
    return (yield Gather(t1, t2))
```

| Handler | Behavior | Elapsed |
|---------|----------|---------|
| `sync_await_handler` | Sequential — each Await blocks | ~0.2s |
| `async_await_handler` | Concurrent — both tasks on event loop | ~0.1s |

## When to Use Await vs Wait

| Use `Await` | Use `Wait` |
|-------------|------------|
| Python coroutines (`async def`) | doeff `Task` / `Future` |
| `asyncio.sleep()` | Spawned doeff programs |
| `aiohttp`, `httpx` calls | `yield Spawn(...)` results |
| Third-party async libraries | doeff-native concurrency |

## Combining with Spawn

To run multiple asyncio coroutines concurrently within doeff:

```python
@do
def parallel_fetches():
    # Await is a single-effect program — can be spawned
    t1 = yield Spawn(Await(fetch_url("https://a.com")))
    t2 = yield Spawn(Await(fetch_url("https://b.com")))
    results = yield Gather(t1, t2)
    return results
```

This works because in doeff, an effect IS a program. `Await(coro)` is a
single-effect program that the scheduler can manage as a task.

**Important:** Concurrent overlap requires `async_await_handler` under
`async_run()`. With `sync_await_handler`, spawned Await tasks execute
sequentially.

## Interaction with Scheduler

When `Await` is used inside a scheduled task (via Spawn), the handler
converts it to an ExternalPromise + Wait. The scheduler treats the Wait
like any other: the task is parked, and the scheduler advances to other
runnable tasks. When the promise completes (from the background thread or
asyncio task), the scheduler wakes the waiting task.

## Examples

### Basic HTTP fetch

```python
import aiohttp

@do
def fetch_json(url):
    session = aiohttp.ClientSession()
    try:
        response = yield Await(session.get(url))
        data = yield Await(response.json())
        return data
    finally:
        yield Await(session.close())
```

### Timeout via asyncio

```python
import asyncio

@do
def with_timeout(program, seconds):
    task = yield Spawn(program)
    try:
        result = yield Await(asyncio.wait_for(
            asyncio.shield(asyncio.sleep(seconds)),
            timeout=seconds
        ))
        yield Cancel(task)
        raise TimeoutError()
    except asyncio.TimeoutError:
        raise TimeoutError()
```

## Related Specs

| Spec | Relationship |
|------|-------------|
| SPEC-SCHED-001 | Scheduler handles Spawn/Wait/Gather — `Await` is orthogonal |
| SPEC-008 | `PythonAsyncSyntaxEscape` DoCtrl handles the VM-level escape |
| SPEC-EFF-010 | ExternalPromise — the bridge mechanism both handlers use |
| ISSUE-CORE-495 | Tracks implementation of these handler specifications |
