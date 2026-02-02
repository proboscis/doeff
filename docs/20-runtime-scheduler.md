# 20. Runtime and Execution Model

doeff provides two main execution functions optimized for different contexts. Choose the right function based on your application's needs—async I/O or synchronous execution.

## Table of Contents

- [Overview](#overview)
- [Execution Functions](#execution-functions)
  - [sync_run](#sync_run)
  - [async_run](#async_run)
- [Choosing an Execution Function](#choosing-an-execution-function)
- [Usage Examples](#usage-examples)
  - [Basic Usage](#basic-usage)
  - [RuntimeResult and Error Handling](#runtimeresult-and-error-handling)
  - [Custom Handlers](#custom-handlers)
- [Cooperative Scheduling](#cooperative-scheduling)
- [Migration Guide](#migration-guide)

---

## Overview

doeff provides two execution functions:

| Function | Execution Model | Use Case |
|----------|-----------------|----------|
| `sync_run` | Synchronous with cooperative scheduling | CLI tools, scripts, testing |
| `async_run` | Real async I/O with `asyncio` | Production applications, HTTP/DB operations |

Both functions return `RuntimeResult`:
- `sync_run(program, handlers, env, store)` - Execute synchronously
- `async_run(program, handlers, env, store)` - Execute asynchronously

---

## Execution Functions

### async_run

The **primary function** for production applications. Executes effects using real async I/O operations through Python's `asyncio` event loop.

```python
from doeff import do, Delay, Await, async_run, async_handlers_preset
import asyncio

@do
def fetch_data():
    yield Delay(1)  # Real asyncio.sleep()
    response = yield Await(http_client.get("/api/data"))
    return response.json()

async def main():
    result = await async_run(fetch_data(), async_handlers_preset)

    if result.is_ok():
        print(result.value)
    else:
        print(f"Error: {result.error}")

asyncio.run(main())
```

**Supported Effects:**

| Effect | Behavior |
|--------|----------|
| `Delay(seconds)` | Real `asyncio.sleep()` |
| `WaitUntil(datetime)` | Sleep until wall-clock time |
| `GetTime()` | Returns `datetime.now()` |
| `Await(coroutine)` | Await the coroutine |
| `Gather(*programs)` | Run Programs in parallel |
| `Spawn(program)` | Create background task with snapshot semantics |
| `Wait(task)` | Wait for spawned task result |
| `task.cancel()` | Request task cancellation (yields Effect) |
| `task.is_done()` | Check task completion (yields Effect) |

### sync_run

A synchronous execution function for programs that don't require async I/O. Ideal for CLI tools, scripts, and testing pure logic. Supports **cooperative concurrency** via an internal task scheduler.

```python
from doeff import do, Get, Put, sync_run, sync_handlers_preset

@do
def counter():
    count = yield Get("counter")
    yield Put("counter", count + 1)
    return count + 1

result = sync_run(counter(), sync_handlers_preset, store={"counter": 0})

if result.is_ok():
    print(result.value)  # 1
```

#### Cooperative Concurrency (Spawn/Wait/Gather)

`sync_run` supports `Spawn`, `Wait`, and `Gather` via **cooperative scheduling**—no threads, no asyncio. Tasks are interleaved at yield points.

```python
from doeff import do, Spawn, Wait, Gather, sync_run, sync_handlers_preset

@do
def parallel_work():
    # Spawn tasks (enqueues them, returns immediately)
    t1 = yield Spawn(compute_a())
    t2 = yield Spawn(compute_b())
    t3 = yield Spawn(compute_c())

    # Wait for results (scheduler interleaves execution)
    result1 = yield Wait(t1)
    result2 = yield Wait(t2)
    result3 = yield Wait(t3)
    return [result1, result2, result3]

result = sync_run(parallel_work(), sync_handlers_preset)
```

**How it works:**

```
┌─────────────────────────────────────────────────────────┐
│                  SyncRuntime Scheduler                   │
│                                                          │
│   Task Queue: [main] ──Spawn──> [main, task1, task2]    │
│                                                          │
│   Loop:                                                  │
│   1. Pick next ready task                               │
│   2. Run until yield (effect)                           │
│   3. Handle effect:                                     │
│      - Spawn → enqueue new task, return handle          │
│      - Wait  → block task until target completes        │
│      - Gather → block until all targets complete        │
│      - Other → process normally                         │
│   4. Repeat until all tasks done                        │
└─────────────────────────────────────────────────────────┘
```

This is **cooperative concurrency** (not parallelism):
- Single-threaded task execution, no race conditions
- Deterministic execution order (for pure compute)
- Tasks yield control at every `yield` statement
- No asyncio, no kernel I/O multiplexing—pure Python scheduling

#### Delay with Timer Thread

`Delay` uses a background timer thread so other tasks can run while waiting:

```
Main Thread (Scheduler)              Timer Thread
───────────────────────              ────────────
task1: yield Delay(2.0)
   ├──► mark task1 "blocked"
   ├──► submit timer ──────────────► time.sleep(2.0)
   ▼
task2: running...                         │
task3: running...                         │
   │                                      │
   │    ◄──── wake_task1() ◄──────────────┘
   ▼
task1: ready, resumes
```

- Timer threads handle `Delay` without blocking the scheduler
- Task execution remains single-threaded (no races in user code)
- Only timing is offloaded to threads

**Limitations:**

- `Await` - Not supported (requires asyncio event loop). Use `AsyncRuntime` for Python coroutines.
- No true CPU parallelism (tasks run sequentially on main thread)

### Time Effects in sync_run

`sync_run` handles time effects with real wall-clock time:

```python
from doeff import do, Delay, GetTime, sync_run, sync_handlers_preset

@do
def timed_operation():
    start = yield GetTime()
    yield Delay(1)  # Real 1 second delay
    end = yield GetTime()
    return f"Elapsed: {(end - start).total_seconds()}s"

result = sync_run(timed_operation(), sync_handlers_preset)
print(result.value)  # "Elapsed: ~1.0s"
```

**Note:** For testing time-dependent logic without real delays, mock the time effects with custom handlers.

---

## Choosing an Execution Function

| If you need... | Use |
|----------------|-----|
| Real HTTP/DB/file I/O | `async_run` |
| Real wall-clock delays | `async_run` or `sync_run` |
| True parallel execution (CPU concurrency) | `async_run` |
| Python async/await interop (`Await`) | `async_run` |
| Concurrent tasks without asyncio | `sync_run` |
| CLI tools / scripts | `sync_run` |
| Deterministic concurrent testing | `sync_run` |

### Feature Comparison

| Feature | `async_run` | `sync_run` |
|---------|-------------|------------|
| Call style | `await async_run(...)` | `sync_run(...)` |
| `Delay` effect | Real `asyncio.sleep` | Real `time.sleep` with timer threads |
| `WaitUntil` effect | Real async wait | Real blocking wait |
| `GetTime` effect | `datetime.now()` | `datetime.now()` |
| `Await` effect | Real await | Runs in background thread |
| `Spawn` effect | Background task (asyncio) | Cooperative (task queue) |
| `Wait` effect | Async wait | Cooperative scheduling |
| `Gather` effect | Parallel (true concurrency) | Cooperative (interleaved) |
| Deterministic | No | Yes (for pure compute) |
| Parallelism | Yes (kernel-level) | No (single-threaded) |

---

## Usage Examples

### Basic Usage

**async_run (async context):**
```python
import asyncio
from doeff import do, Ask, async_run, async_handlers_preset

@do
def greet():
    name = yield Ask("name")
    return f"Hello, {name}!"

async def main():
    result = await async_run(greet(), async_handlers_preset, env={"name": "World"})

    if result.is_ok():
        print(result.value)  # "Hello, World!"

asyncio.run(main())
```

**sync_run (sync context):**
```python
from doeff import do, Ask, sync_run, sync_handlers_preset

@do
def greet():
    name = yield Ask("name")
    return f"Hello, {name}!"

result = sync_run(greet(), sync_handlers_preset, env={"name": "World"})

if result.is_ok():
    print(result.value)  # "Hello, World!"
```

**Testing with sync_run:**
```python
from doeff import do, Delay, sync_run, sync_handlers_preset

@do
def delayed_response():
    yield Delay(0.1)  # Short delay for testing
    return "Ready!"

result = sync_run(delayed_response(), sync_handlers_preset)

assert result.is_ok()
assert result.value == "Ready!"
```

### RuntimeResult and Error Handling

Both `sync_run` and `async_run` return `RuntimeResult[T]` which wraps the computation outcome with full debugging context:

```python
from doeff import do, sync_run, sync_handlers_preset

@do
def risky_operation():
    raise ValueError("Something went wrong")
    return 42  # Never reached

result = sync_run(risky_operation(), sync_handlers_preset)

# Check result - NOTE: is_ok() and is_err() are METHODS, not properties!
if result.is_ok():
    print(f"Success: {result.value}")
else:
    print(f"Error: {result.error}")

    # Access debugging information
    print(result.format())  # Condensed output
    print(result.format(verbose=True))  # Full debugging output

    # Individual stack traces
    print(result.python_stack.format())   # Python source locations
    print(result.effect_stack.format())   # Effect call tree
    print(result.k_stack.format())        # Continuation stack
```

**RuntimeResult API:**

| Property/Method | Description |
|-----------------|-------------|
| `result` | `Result[T]`: Raw `Ok(value)` or `Err(error)` |
| `value` | `T`: Unwrap Ok value (raises if Err) |
| `error` | `BaseException`: Get error (raises if Ok) |
| `is_ok()` | `bool`: True if execution succeeded (METHOD!) |
| `is_err()` | `bool`: True if execution failed (METHOD!) |
| `raw_store` | `dict`: Final store state |
| `env` | `dict`: Final environment |
| `k_stack` | `KStackTrace`: Continuation stack at termination |
| `effect_stack` | `EffectStackTrace`: Effect call tree |

**Accessing logs and graph:**
```python
logs = result.raw_store.get("__log__", [])
graph = result.raw_store.get("__graph__")
```
| `python_stack` | `PythonStackTrace`: Python source locations |
| `format()` | `str`: Human-readable condensed output |
| `format(verbose=True)` | `str`: Full debugging output |

### Custom Handlers

Extend runtime behavior by providing custom effect handlers. Handlers in doeff are `@do` functions that take an effect and a `HandlerContext`, returning either a `CESKState` or a plain value:

```python
from dataclasses import dataclass
from doeff import do, sync_run, sync_handlers_preset
from doeff._types_internal import EffectBase
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.state import CESKState

@dataclass(frozen=True)
class CustomLog(EffectBase):
    message: str

@do
def custom_log_handler(effect: EffectBase, ctx: HandlerContext):
    """Custom handler for CustomLog effect."""
    if isinstance(effect, CustomLog):
        print(f"[LOG] {effect.message}")
        return CESKState.with_value(None, ctx.env, ctx.store, ctx.k)

    # Forward unhandled effects to outer handlers
    result = yield effect
    return result

@do
def program_with_logging():
    yield CustomLog(message="Starting...")
    yield CustomLog(message="Processing...")
    return "Done"

# Prepend custom handler to preset (handlers are outermost to innermost)
handlers = [custom_log_handler, *sync_handlers_preset]
result = sync_run(program_with_logging(), handlers)
```

**Handler Signature:**
```python
@do
def my_handler(effect: EffectBase, ctx: HandlerContext) -> CESKState | Any:
    # ctx.store: Current store (mutable state)
    # ctx.env: Current environment (reader context)
    # ctx.k: Full continuation stack
    # ctx.delimited_k: Continuation up to this handler
    ...
```

See [Handler API](handler-api.md) for detailed handler documentation.

---

## Testing with Controlled Time

For testing time-dependent logic, you can use custom handlers or set the initial `__current_time__` in the store:

```python
from datetime import datetime
from doeff import do, GetTime, sync_run, sync_handlers_preset

@do
def get_current_time():
    current = yield GetTime()
    return current

# Control time via store
result = sync_run(
    get_current_time(),
    sync_handlers_preset,
    store={"__current_time__": datetime(2024, 1, 1, 12, 0)}
)
print(result.value)  # datetime(2024, 1, 1, 12, 0)
```

For more sophisticated time control (advancing time during execution), create a custom handler that intercepts time effects.

---

## Migration Guide

### From Runtime Classes to Functions

The old `Runtime` classes have been replaced with simple functions:

**Before (deprecated):**
```python
from doeff.runtimes import AsyncioRuntime, SyncRuntime

# Async
runtime = AsyncioRuntime()
result = await runtime.run(program)

# Sync
runtime = SyncRuntime()
result = runtime.run(program)
```

**After:**
```python
from doeff import sync_run, async_run, sync_handlers_preset, async_handlers_preset

# Sync
result = sync_run(program, sync_handlers_preset)

# Async
result = await async_run(program, async_handlers_preset)

# Result is RuntimeResult
if result.is_ok():
    value = result.value
```

### From run_safe() to RuntimeResult

`run_safe()` no longer exists. The run functions always return `RuntimeResult`:

**Before (deprecated):**
```python
# run() raised on error
result = runtime.run(program)

# run_safe() returned RuntimeResult
result = runtime.run_safe(program)
if result.is_ok:  # was a property
    value = result.unwrap()
```

**After:**
```python
# Functions always return RuntimeResult
result = sync_run(program, sync_handlers_preset)

# NOTE: is_ok() is a METHOD now!
if result.is_ok():
    value = result.value  # .value, not .unwrap()
```

### API Changes Summary

| Old API | New API |
|---------|---------|
| `from doeff.runtimes import AsyncioRuntime` | `from doeff import async_run, async_handlers_preset` |
| `from doeff.runtimes import SyncRuntime` | `from doeff import sync_run, sync_handlers_preset` |
| `runtime = SyncRuntime(); runtime.run(p)` | `sync_run(p, sync_handlers_preset)` |
| `runtime = AsyncRuntime(); await runtime.run(p)` | `await async_run(p, async_handlers_preset)` |
| `result.is_ok` (property) | `result.is_ok()` (method) |
| `result.is_err` (property) | `result.is_err()` (method) |
| `result.unwrap()` | `result.value` |
| `result.unwrap_err()` | `result.error` |
| `result.effect_traceback` | `result.effect_stack`, `result.python_stack`, `result.k_stack` |

### Handler Signature Changes

Effect handlers now use `@do` decorator with `HandlerContext`:

**Before (v1):**
```python
def my_handler(effect, env, store, k, scheduler):
    return Resume(value, store)
```

**After (v2):**
```python
from doeff import do
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.state import CESKState

@do
def my_handler(effect, ctx: HandlerContext):
    if isinstance(effect, MyEffect):
        return CESKState.with_value(result, ctx.env, ctx.store, ctx.k)
    # Forward unhandled effects
    result = yield effect
    return result
```

---

## See Also

- [Effects Matrix](21-effects-matrix.md) - Complete effect support reference
- [Error Handling](05-error-handling.md) - RuntimeResult and Safe effect
- [Async Effects](04-async-effects.md) - Gather, Spawn, Time effects
