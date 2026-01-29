# 20. Runtime Variants

The `doeff.cesk.runtime` module provides three distinct runtime implementations optimized for different execution contexts. Choose the right runtime based on your application's needs—real async I/O, synchronous execution, or simulated time for testing.

## Table of Contents

- [Overview](#overview)
- [Runtime Types](#runtime-types)
  - [AsyncRuntime](#asyncruntime)
  - [SyncRuntime](#syncruntime)
  - [SimulationRuntime](#simulationruntime)
- [Choosing a Runtime](#choosing-a-runtime)
- [Usage Examples](#usage-examples)
  - [Basic Usage](#basic-usage)
  - [RuntimeResult and Error Handling](#runtimeresult-and-error-handling)
  - [Custom Handlers](#custom-handlers)
- [Simulation Features](#simulation-features)
  - [Time Advancement](#time-advancement)
  - [Mocking Awaitables](#mocking-awaitables)
- [Migration Guide](#migration-guide)

---

## Overview

Each runtime implementation determines **how** effects are executed:

| Runtime | Execution Model | Use Case |
|---------|-----------------|----------|
| `AsyncRuntime` | Real async I/O with `asyncio` | Production applications, HTTP/DB operations |
| `SyncRuntime` | Pure synchronous, no event loop | CLI tools, scripts, simple utilities |
| `SimulationRuntime` | Simulated time (instant) | Testing, backtesting, deterministic replay |

All runtimes share a common interface and return `RuntimeResult`:
- `run(program, env, store)` - Execute program, return `RuntimeResult[T]`
- `run_and_unwrap(program, env, store)` - Execute and return raw value (raises on error)

---

## Runtime Types

### AsyncRuntime

The **primary runtime** for production applications. Executes effects using real async I/O operations through Python's `asyncio` event loop.

```python
from doeff import do, Program, Delay, Await
from doeff.cesk.runtime import AsyncRuntime
import asyncio

@do
def fetch_data() -> Program[dict]:
    yield Delay(seconds=1)  # Real asyncio.sleep()
    response = yield Await(http_client.get("/api/data"))
    return response.json()

async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(fetch_data())
    
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
| `task.join()` | Wait for spawned task |
| `task.cancel()` | Request task cancellation |
| `task.is_done()` | Check task completion |

### SyncRuntime

A synchronous runtime for programs that don't require async I/O. Ideal for CLI tools, scripts, and testing pure logic. Supports **cooperative concurrency** via an internal task scheduler.

```python
from doeff import do, Program, Get, Put
from doeff.cesk.runtime import SyncRuntime

@do
def counter() -> Program[int]:
    count = yield Get("counter")
    yield Put("counter", count + 1)
    return count + 1

runtime = SyncRuntime()
result = runtime.run(counter(), store={"counter": 0})

if result.is_ok():
    print(result.value)  # 1
```

#### Cooperative Concurrency (Spawn/Wait/Gather)

`SyncRuntime` supports `Spawn`, `Wait`, and `Gather` via **cooperative scheduling**—no threads, no asyncio. Tasks are interleaved at yield points.

```python
from doeff import do, Program, Spawn, Wait, Gather

@do
def parallel_work() -> Program[list[int]]:
    # Spawn tasks (enqueues them, returns immediately)
    t1 = yield Spawn(compute_a())
    t2 = yield Spawn(compute_b())
    t3 = yield Spawn(compute_c())
    
    # Gather waits for all (scheduler interleaves execution)
    results = yield Gather(t1, t2, t3)
    return results

runtime = SyncRuntime()
result = runtime.run(parallel_work())
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

### SimulationRuntime

A discrete event simulation runtime where time advances instantly. Perfect for testing time-dependent logic, backtesting strategies, or deterministic replay.

```python
from datetime import datetime, timedelta
from doeff import do, Program, Delay
from doeff.cesk.runtime import SimulationRuntime

@do
def hourly_check() -> Program[str]:
    yield Delay(seconds=3600)  # 1 hour - instant in simulation
    yield Delay(seconds=3600)  # Another hour - still instant
    return "Two hours passed"

runtime = SimulationRuntime(start_time=datetime(2024, 1, 1, 9, 0))
result = runtime.run(hourly_check())

if result.is_ok():
    print(result.value)  # "Two hours passed"
    print(runtime.current_time)  # datetime(2024, 1, 1, 11, 0) - 2 hours advanced
```

**Key Features:**
- Time advances only when the simulation processes `Delay` or `WaitUntil` effects
- `Gather` runs sequentially within the simulation
- Awaitables can be mocked to return predetermined results

---

## Choosing a Runtime

| If you need... | Use |
|----------------|-----|
| Real HTTP/DB/file I/O | `AsyncRuntime` |
| Real wall-clock delays | `AsyncRuntime` |
| True parallel execution (CPU concurrency) | `AsyncRuntime` |
| Python async/await interop (`Await`) | `AsyncRuntime` |
| Concurrent tasks without asyncio | `SyncRuntime` |
| CLI tools / scripts | `SyncRuntime` |
| Deterministic concurrent testing | `SyncRuntime` or `SimulationRuntime` |
| Unit tests (fast, deterministic) | `SimulationRuntime` |
| Backtesting trading strategies | `SimulationRuntime` |
| Mocking async operations | `SimulationRuntime` |

### Feature Comparison

| Feature | AsyncRuntime | SyncRuntime | SimulationRuntime |
|---------|----------------|-------------|-------------------|
| `run()` method | `async` | sync | sync |
| `Delay` effect | Real sleep | Real sleep (blocking) | Instant (time advances) |
| `WaitUntil` effect | Real wait | Real wait (blocking) | Instant (time advances) |
| `GetTime` effect | `datetime.now()` | `datetime.now()` | Simulated time |
| `Await` effect | Real await | Not supported | Mocked result |
| `Spawn` effect | Background task (asyncio) | Cooperative (task queue) | Cooperative (task queue) |
| `Wait` effect | Async wait | Cooperative scheduling | Cooperative scheduling |
| `Gather` effect | Parallel (true concurrency) | Cooperative (interleaved) | Cooperative (interleaved) |
| Time control | No | No | Yes (`current_time`) |
| Deterministic | No | Yes | Yes |
| Parallelism | Yes (kernel-level) | No (single-threaded) | No (single-threaded) |

---

## Usage Examples

### Basic Usage

**AsyncRuntime (async context):**
```python
import asyncio
from doeff import do, Program, Ask
from doeff.cesk.runtime import AsyncRuntime

@do
def greet() -> Program[str]:
    name = yield Ask("name")
    return f"Hello, {name}!"

async def main():
    runtime = AsyncRuntime()
    result = await runtime.run(greet(), env={"name": "World"})
    
    if result.is_ok():
        print(result.value)  # "Hello, World!"

asyncio.run(main())
```

**SyncRuntime (sync context):**
```python
from doeff import do, Program, Ask
from doeff.cesk.runtime import SyncRuntime

@do
def greet() -> Program[str]:
    name = yield Ask("name")
    return f"Hello, {name}!"

runtime = SyncRuntime()
result = runtime.run(greet(), env={"name": "World"})

if result.is_ok():
    print(result.value)  # "Hello, World!"
```

**SimulationRuntime (testing):**
```python
from datetime import datetime
from doeff import do, Program, Delay
from doeff.cesk.runtime import SimulationRuntime

@do
def delayed_response() -> Program[str]:
    yield Delay(seconds=60)
    return "Ready!"

runtime = SimulationRuntime(start_time=datetime(2024, 1, 1))
result = runtime.run(delayed_response())

assert result.is_ok()
assert result.value == "Ready!"
assert runtime.current_time == datetime(2024, 1, 1, 0, 1)  # 1 minute advanced
```

### RuntimeResult and Error Handling

All runtimes return `RuntimeResult[T]` which wraps the computation outcome with full debugging context:

```python
from doeff import do, Program
from doeff.cesk.runtime import SyncRuntime

@do
def risky_operation() -> Program[int]:
    raise ValueError("Something went wrong")
    return 42  # Never reached

runtime = SyncRuntime()
result = runtime.run(risky_operation())

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
| `state` | `dict`: Final state from Put/Modify effects |
| `log` | `list`: Accumulated Tell/Log messages |
| `env` | `dict`: Final environment |
| `k_stack` | `KStackTrace`: Continuation stack at termination |
| `effect_stack` | `EffectStackTrace`: Effect call tree |
| `python_stack` | `PythonStackTrace`: Python source locations |
| `format()` | `str`: Human-readable condensed output |
| `format(verbose=True)` | `str`: Full debugging output |

### Custom Handlers

Extend runtime behavior by providing custom effect handlers:

```python
from dataclasses import dataclass
from doeff import do, Program
from doeff._types_internal import EffectBase
from doeff.cesk.runtime import SyncRuntime
from doeff.cesk.handlers import default_handlers
from doeff.cesk.frames import ContinueValue

@dataclass(frozen=True)
class CustomLog(EffectBase):
    message: str

def handle_custom_log(effect, task_state, store):
    """Custom handler for CustomLog effect."""
    if isinstance(effect, CustomLog):
        print(f"[LOG] {effect.message}")
        return ContinueValue(
            value=None,
            env=task_state.env,
            store=store,
            k=task_state.kontinuation,
        )
    return None

@do
def program_with_logging() -> Program[str]:
    yield CustomLog(message="Starting...")
    yield CustomLog(message="Processing...")
    return "Done"

# Add custom handler to defaults
handlers = default_handlers()
handlers[CustomLog] = handle_custom_log

runtime = SyncRuntime(handlers=handlers)
result = runtime.run(program_with_logging())
```

---

## Simulation Features

### Time Advancement

`SimulationRuntime` tracks a virtual clock that advances when time-based effects are processed:

```python
from datetime import datetime, timedelta
from doeff import do, Program, Delay, WaitUntil, GetTime
from doeff.cesk.runtime import SimulationRuntime

@do
def time_travel() -> Program[datetime]:
    start = yield GetTime()
    yield Delay(seconds=3600)  # +1 hour
    yield Delay(seconds=1800)  # +30 minutes
    
    target = datetime(2024, 1, 1, 12, 0)
    yield WaitUntil(target)    # Jump to noon
    
    end = yield GetTime()
    return end

start = datetime(2024, 1, 1, 9, 0)
runtime = SimulationRuntime(start_time=start)
result = runtime.run(time_travel())

if result.is_ok():
    print(result.value)  # datetime(2024, 1, 1, 12, 0)
    print(runtime.current_time)  # datetime(2024, 1, 1, 12, 0)
```

### Mocking Awaitables

Mock async operations to return predetermined results:

```python
from doeff import do, Program, Await
from doeff.cesk.runtime import SimulationRuntime

@do
def fetch_user() -> Program[dict]:
    response = yield Await(session.get("/api/user/123"))
    return response

# Mock the coroutine type to return test data
runtime = SimulationRuntime()
runtime.mock(type(session.get("/api/user/123")), {"id": 123, "name": "Test User"})

result = runtime.run(fetch_user())
if result.is_ok():
    print(result.value)  # {"id": 123, "name": "Test User"}
```

---

## Migration Guide

### From AsyncioRuntime to AsyncRuntime

The runtime class has been renamed and moved:

**Before (deprecated):**
```python
from doeff.runtimes import AsyncioRuntime

runtime = AsyncioRuntime()
result = await runtime.run(program)
```

**After:**
```python
from doeff.cesk.runtime import AsyncRuntime

runtime = AsyncRuntime()
result = await runtime.run(program)

# Result is now RuntimeResult, not raw value
if result.is_ok():
    value = result.value
```

### From run_safe() to run()

`run_safe()` no longer exists. All runtimes now return `RuntimeResult` from `run()`:

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
# run() always returns RuntimeResult
result = runtime.run(program)

# NOTE: is_ok() is a METHOD now!
if result.is_ok():
    value = result.value  # .value, not .unwrap()
```

### API Changes Summary

| Old API | New API |
|---------|---------|
| `from doeff.runtimes import AsyncioRuntime` | `from doeff.cesk.runtime import AsyncRuntime` |
| `from doeff.runtimes import SyncRuntime` | `from doeff.cesk.runtime import SyncRuntime` |
| `from doeff.runtimes import SimulationRuntime` | `from doeff.cesk.runtime import SimulationRuntime` |
| `runtime.run(program)` → raises on error | `runtime.run(program)` → returns `RuntimeResult` |
| `runtime.run_safe(program)` | Removed, use `runtime.run(program)` |
| `result.is_ok` (property) | `result.is_ok()` (method) |
| `result.is_err` (property) | `result.is_err()` (method) |
| `result.unwrap()` | `result.value` |
| `result.unwrap_err()` | `result.error` |
| `result.effect_traceback` | `result.effect_stack`, `result.python_stack`, `result.k_stack` |

### Handler Signature Changes

Effect handlers now use a simpler signature:

**Before:**
```python
def my_handler(effect, env, store, k, scheduler):
    return Resume(value, store)
```

**After:**
```python
from doeff.cesk.frames import ContinueValue

def my_handler(effect, task_state, store):
    return ContinueValue(
        value=value,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )
```

---

## See Also

- [Effects Matrix](21-effects-matrix.md) - Complete effect support reference
- [Error Handling](05-error-handling.md) - RuntimeResult and Safe effect
- [Async Effects](04-async-effects.md) - Gather, Spawn, Time effects
