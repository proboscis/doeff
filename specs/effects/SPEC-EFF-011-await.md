# SPEC-EFF-011: Await Effect (asyncio Bridge)

## Status: Draft

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
class AwaitEffect(EffectBase):
    awaitable: Awaitable[Any]
```

Usage: `result = yield Await(some_coroutine())`

## Semantics

1. The VM receives the `Await` effect via `PythonAsyncSyntaxEscape` (DoCtrl)
2. The VM suspends the current generator and delegates to the Python async
   runtime to await the coroutine
3. When the coroutine completes, the result is sent back into the generator
4. If the coroutine raises, the exception is thrown into the generator

`Await` is a **blocking** operation from the doeff program's perspective —
the generator is suspended until the coroutine completes.

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

## Handler Requirements

`Await` requires an async-capable handler. It is handled via the VM's
`PythonAsyncSyntaxEscape` mechanism, which delegates to `async_run`'s
event loop.

| Context | Support |
|---------|---------|
| `async_run` | Full support — delegates to asyncio event loop |
| `run` (sync) | NOT supported — raises unhandled effect |

## Interaction with Scheduler

When `Await` is used inside a scheduled task (via Spawn), the scheduler
treats it like any other effect: the envelope inserts a `SchedulerYield`
after the `Await` completes, allowing other tasks to run.

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
