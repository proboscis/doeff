# 20. Runtime Variants

The `doeff.runtimes` module provides three distinct runtime implementations optimized for different execution contexts. Choose the right runtime based on your application's needs—real async I/O, synchronous execution, or simulated time for testing.

## Table of Contents

- [Overview](#overview)
- [Runtime Types](#runtime-types)
  - [AsyncioRuntime](#asyncioruntime)
  - [SyncRuntime](#syncruntime)
  - [SimulationRuntime](#simulationruntime)
- [Choosing a Runtime](#choosing-a-runtime)
- [Usage Examples](#usage-examples)
  - [Basic Usage](#basic-usage)
  - [Error Handling with run_safe()](#error-handling-with-run_safe)
  - [Custom Handlers](#custom-handlers)
- [Simulation Features](#simulation-features)
  - [Time Advancement](#time-advancement)
  - [Mocking Awaitables](#mocking-awaitables)
  - [Spawning Concurrent Tasks](#spawning-concurrent-tasks)
- [Migration Guide](#migration-guide)

---

## Overview

Each runtime implementation determines **how** effects are executed:

| Runtime | Execution Model | Use Case |
|---------|-----------------|----------|
| `AsyncioRuntime` | Real async I/O with `asyncio` | Production applications, HTTP/DB operations |
| `SyncRuntime` | Pure synchronous, no event loop | CLI tools, scripts, simple utilities |
| `SimulationRuntime` | Simulated time (instant) | Testing, backtesting, deterministic replay |

All runtimes share a common interface:
- `run(program, env, store)` - Execute program, raise `EffectError` on failure
- `run_safe(program, env, store)` - Execute program, return `RuntimeResult` instead of raising

---

## Runtime Types

### AsyncioRuntime

The primary runtime for production applications. Executes effects using real async I/O operations through Python's `asyncio` event loop.

```python
from doeff import do, Program
from doeff.effects import Delay, Await
from doeff.runtimes import AsyncioRuntime

@do
def fetch_data() -> Program[dict]:
    yield Delay(seconds=1)  # Real asyncio.sleep()
    response = yield Await(http_client.get("/api/data"))
    return response.json()

async def main():
    runtime = AsyncioRuntime()
    result = await runtime.run(fetch_data())
    print(result)
```

**Supported Effects:**

| Effect | Behavior |
|--------|----------|
| `Delay(seconds)` | Real `asyncio.sleep()` |
| `WaitUntil(datetime)` | Sleep until wall-clock time |
| `Await(coroutine)` | Await the coroutine |
| `Spawn(program)` | Create background `asyncio.Task` |

### SyncRuntime

A synchronous runtime for programs that don't require async I/O. Ideal for CLI tools, scripts, and testing pure logic.

```python
from doeff import do, Program
from doeff.effects import Get, Put
from doeff.runtimes import SyncRuntime

@do
def counter() -> Program[int]:
    count = yield Get("counter")
    yield Put("counter", count + 1)
    return count + 1

runtime = SyncRuntime()
result = runtime.run(counter(), store={"counter": 0})
print(result)  # 1
```

**Limitations:**

`SyncRuntime` raises `AsyncEffectInSyncRuntimeError` for async-only effects:
- `Await` - Use `AsyncioRuntime` instead
- `WaitUntil` - Use `AsyncioRuntime` instead
- `Spawn` - Use `AsyncioRuntime` instead

`Delay` uses `time.sleep()` in SyncRuntime (blocks the thread).

### SimulationRuntime

A discrete event simulation runtime where time advances instantly. Perfect for testing time-dependent logic, backtesting strategies, or deterministic replay.

```python
from datetime import datetime, timedelta
from doeff import do, Program
from doeff.effects import Delay
from doeff.runtimes import SimulationRuntime

@do
def hourly_check() -> Program[str]:
    yield Delay(seconds=3600)  # 1 hour - instant in simulation
    yield Delay(seconds=3600)  # Another hour - still instant
    return "Two hours passed"

runtime = SimulationRuntime(start_time=datetime(2024, 1, 1, 9, 0))
result = runtime.run(hourly_check())
print(result)  # "Two hours passed"
print(runtime.current_time)  # datetime(2024, 1, 1, 11, 0) - 2 hours advanced
```

**Key Features:**
- Time advances only when the simulation processes `Delay` or `WaitUntil` effects
- Spawned tasks run concurrently within the simulation
- Awaitables can be mocked to return predetermined results

---

## Choosing a Runtime

| If you need... | Use |
|----------------|-----|
| Real HTTP/DB/file I/O | `AsyncioRuntime` |
| Real wall-clock delays | `AsyncioRuntime` |
| Background tasks (`asyncio.Task`) | `AsyncioRuntime` |
| Simple sync execution (no async) | `SyncRuntime` |
| CLI tools / scripts | `SyncRuntime` |
| Unit tests (fast, deterministic) | `SimulationRuntime` |
| Backtesting trading strategies | `SimulationRuntime` |
| Mocking async operations | `SimulationRuntime` |

### Feature Comparison

| Feature | AsyncioRuntime | SyncRuntime | SimulationRuntime |
|---------|----------------|-------------|-------------------|
| `run()` method | `async` | sync | sync |
| `Delay` effect | Real sleep | Real sleep (blocking) | Instant (time advances) |
| `WaitUntil` effect | Real wait | Error | Instant (time advances) |
| `Await` effect | Real await | Error | Mocked result |
| `Spawn` effect | `asyncio.Task` | Error | Simulated concurrent task |
| Time control | No | No | Yes (`current_time`) |
| Deterministic | No | Yes | Yes |

---

## Usage Examples

### Basic Usage

**AsyncioRuntime (async context):**
```python
import asyncio
from doeff import do, Program
from doeff.effects import Ask
from doeff.runtimes import AsyncioRuntime

@do
def greet() -> Program[str]:
    name = yield Ask("name")
    return f"Hello, {name}!"

async def main():
    runtime = AsyncioRuntime()
    result = await runtime.run(greet(), env={"name": "World"})
    print(result)  # "Hello, World!"

asyncio.run(main())
```

**SyncRuntime (sync context):**
```python
from doeff import do, Program
from doeff.effects import Ask
from doeff.runtimes import SyncRuntime

@do
def greet() -> Program[str]:
    name = yield Ask("name")
    return f"Hello, {name}!"

runtime = SyncRuntime()
result = runtime.run(greet(), env={"name": "World"})
print(result)  # "Hello, World!"
```

**SimulationRuntime (testing):**
```python
from datetime import datetime
from doeff import do, Program
from doeff.effects import Delay
from doeff.runtimes import SimulationRuntime

@do
def delayed_response() -> Program[str]:
    yield Delay(seconds=60)
    return "Ready!"

runtime = SimulationRuntime(start_time=datetime(2024, 1, 1))
result = runtime.run(delayed_response())
assert result == "Ready!"
assert runtime.current_time == datetime(2024, 1, 1, 0, 1)  # 1 minute advanced
```

### Error Handling with run_safe()

All runtimes provide `run_safe()` which returns a `RuntimeResult` instead of raising exceptions:

```python
from doeff import do, Program
from doeff.runtimes import SyncRuntime, RuntimeResult

@do
def risky_operation() -> Program[int]:
    raise ValueError("Something went wrong")
    return 42  # Never reached

runtime = SyncRuntime()
result: RuntimeResult[int] = runtime.run_safe(risky_operation())

if result.is_ok:
    print(f"Success: {result.unwrap()}")
else:
    print(f"Error: {result.unwrap_err()}")
    if result.effect_traceback:
        print(f"Traceback:\n{result.effect_traceback}")
```

**RuntimeResult API:**

| Property/Method | Description |
|-----------------|-------------|
| `is_ok` | `True` if execution succeeded |
| `is_err` | `True` if execution failed |
| `unwrap()` | Get value or raise if error |
| `unwrap_err()` | Get error or raise if ok |
| `display()` | Human-readable result string |
| `effect_traceback` | Effect-level traceback (if error) |

### Custom Handlers

Extend runtime behavior by providing custom effect handlers:

```python
from dataclasses import dataclass
from doeff import do, Program
from doeff.types import EffectBase
from doeff.runtime import Resume
from doeff.runtimes import SyncRuntime

@dataclass(frozen=True, kw_only=True)
class CustomLog(EffectBase):
    message: str

def handle_custom_log(effect, env, store, k, scheduler):
    if isinstance(effect, CustomLog):
        print(f"[LOG] {effect.message}")
        return Resume(None, store)
    return None

@do
def program_with_logging() -> Program[str]:
    yield CustomLog(message="Starting...")
    yield CustomLog(message="Processing...")
    return "Done"

custom_handlers = {CustomLog: handle_custom_log}
runtime = SyncRuntime(handlers=custom_handlers)
result = runtime.run(program_with_logging())
```

---

## Simulation Features

### Time Advancement

`SimulationRuntime` tracks a virtual clock that advances when time-based effects are processed:

```python
from datetime import datetime, timedelta
from doeff import do, Program
from doeff.effects import Delay, WaitUntil
from doeff.runtimes import SimulationRuntime

@do
def time_travel() -> Program[datetime]:
    yield Delay(seconds=3600)  # +1 hour
    yield Delay(seconds=1800)  # +30 minutes
    target = datetime(2024, 1, 1, 12, 0)
    yield WaitUntil(target)    # Jump to noon
    return target

start = datetime(2024, 1, 1, 9, 0)
runtime = SimulationRuntime(start_time=start)
result = runtime.run(time_travel())

print(runtime.current_time)  # datetime(2024, 1, 1, 12, 0)
```

### Mocking Awaitables

Mock async operations to return predetermined results:

```python
import aiohttp
from doeff import do, Program
from doeff.effects import Await
from doeff.runtimes import SimulationRuntime

@do
def fetch_user() -> Program[dict]:
    response = yield Await(session.get("/api/user/123"))
    return response

# Mock the coroutine type to return test data
runtime = SimulationRuntime()
runtime.mock(aiohttp.ClientResponse, {"id": 123, "name": "Test User"})

result = runtime.run(fetch_user())
print(result)  # {"id": 123, "name": "Test User"}
```

### Spawning Concurrent Tasks

`Spawn` creates simulated concurrent tasks that run within the same simulation:

```python
from doeff import do, Program
from doeff.effects import Delay, Spawn
from doeff.runtimes import SimulationRuntime

@do
def background_job() -> Program[None]:
    yield Delay(seconds=10)
    print("Background job done")

@do
def main_job() -> Program[str]:
    yield Spawn(background_job())  # Start background task
    yield Delay(seconds=5)
    return "Main done first"

runtime = SimulationRuntime()
result = runtime.run(main_job())
# Both tasks complete within simulation
```

---

## Migration Guide

### From EffectRuntime to New Runtimes

The legacy `EffectRuntime` and `create_runtime()` API has been replaced by explicit runtime classes.

**Before (deprecated):**
```python
from doeff import create_runtime

runtime = create_runtime()
result = await runtime.run(program)
# or
result = runtime.run_sync(program)
```

**After:**
```python
from doeff.runtimes import AsyncioRuntime, SyncRuntime

# For async execution
runtime = AsyncioRuntime()
result = await runtime.run(program)

# For sync execution
runtime = SyncRuntime()
result = runtime.run(program)
```

### From run()/run_sync() to runtime.run()

**Before:**
```python
from doeff.cesk import run, run_sync

# Async
result = await run(program)

# Sync
result = run_sync(program)
```

**After:**
```python
from doeff.runtimes import AsyncioRuntime, SyncRuntime

# Async
runtime = AsyncioRuntime()
result = await runtime.run(program)

# Sync
runtime = SyncRuntime()
result = runtime.run(program)
```

### Handler Migration

Effect handlers now use the unified `ScheduledEffectHandler` signature:

```python
from doeff.runtime import Resume, Suspend, Schedule

def my_handler(effect, env, store, k, scheduler):
    # For immediate resume (pure effects):
    return Resume(value, store)
    
    # For async operations:
    return Suspend(awaitable, store)
    
    # For scheduler-managed effects (simulation):
    return Schedule(payload, store)
```

### API Changes Summary

| Old API | New API |
|---------|---------|
| `create_runtime()` | `AsyncioRuntime()` or `SyncRuntime()` |
| `runtime.run_sync(program)` | `SyncRuntime().run(program)` |
| `await runtime.run(program)` | `await AsyncioRuntime().run(program)` |
| `run(program, scheduler=...)` | Use `SimulationRuntime` |
| `EffectRuntime` class | `AsyncioRuntime`, `SyncRuntime`, `SimulationRuntime` |

---

<details>
<summary><h2>Legacy: Scheduler-Based Architecture (Archived)</h2></summary>

> **Note**: This section documents the previous scheduler-based architecture for reference. New code should use the runtime classes described above.

The previous architecture used pluggable schedulers (`FIFOScheduler`, `PriorityScheduler`, `SimulationScheduler`, `RealtimeScheduler`) with the `Scheduler` protocol:

```python
class Scheduler(Protocol):
    def submit(self, k: Continuation, hint: Any = None) -> None: ...
    def next(self) -> Continuation | None: ...
```

And handlers used `HandlerResult` types:
- `Resume(value, store)` - Resume immediately
- `Suspend(awaitable, store)` - Await async operation
- `Scheduled(store)` - Task submitted to scheduler

This architecture is still available in `doeff.runtime` for advanced use cases but the recommended approach is to use the typed runtime classes.

</details>
