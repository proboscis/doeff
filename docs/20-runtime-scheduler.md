# 20. Runtime Scheduler

The `doeff.runtime` module implements a sophisticated execution model based on single-shot algebraic effects with pluggable schedulers. This architecture enables the same business logic to run in diverse environments—such as discrete event simulations, realtime production systems, or priority-based actor systems—simply by swapping the scheduler implementation.

## Table of Contents

- [Overview](#overview)
- [Core Concepts](#core-concepts)
  - [Continuation](#continuation)
  - [Scheduler Protocol](#scheduler-protocol)
  - [ScheduledEffectHandler](#scheduledeffecthandler)
  - [HandlerResult Types](#handlerresult-types)
- [Reference Schedulers](#reference-schedulers)
  - [FIFOScheduler](#fifoscheduler)
  - [PriorityScheduler](#priorityscheduler)
  - [SimulationScheduler](#simulationscheduler)
  - [RealtimeScheduler](#realtimescheduler)
- [Simulation Effects](#simulation-effects)
- [Usage Examples](#usage-examples)
  - [Basic Usage](#basic-usage)
  - [Simulation vs Realtime](#simulation-vs-realtime)
  - [Custom Handlers](#custom-handlers)
- [Migration Guide](#migration-guide)

---

---

## Overview

Traditional effect handlers in `doeff` typically resume the computation immediately or await an async operation. The `runtime` module introduces a **scheduler-based loop** where handlers can decide to suspend a computation and hand it over to a scheduler for later resumption.

This decoupling of "what happens" (the effect) from "when/how it happens" (the scheduler) is powerful for:
- **Simulation**: Testing complex workflows with discrete event time.
- **Realtime**: Running the same workflows with wall-clock timing in production.
- **Resource Management**: Prioritizing tasks or implementing rate limits.

## Core Concepts

### Continuation

A `Continuation` represents a suspended computation that can be resumed exactly once (**single-shot**).

```python
@dataclass
class Continuation:
    def resume(self, value: Any, store: Store) -> CESKState:
        """Resume with a success value."""
        ...

    def resume_error(self, ex: BaseException, store: Store) -> CESKState:
        """Resume with an error."""
        ...
```

Continuations capture the full execution state (Environment, Store, and Kontinuation stack). In Python, because generators cannot be cloned, attempts to resume a continuation more than once will raise a `RuntimeError`.

### Scheduler Protocol

A `Scheduler` manages a pool of pending continuations and decides which one to run next.

```python
class Scheduler(Protocol):
    def submit(self, k: Continuation, hint: Any = None) -> None:
        """Add a continuation to the pool with an optional hint."""
        ...
    
    def next(self) -> Continuation | None:
        """Pick the next continuation to run, or return None if empty."""
        ...
```

The `hint` parameter is opaque and its meaning depends on the scheduler implementation (e.g., a priority integer, a timestamp, or a delay).

### ScheduledEffectHandler

Handlers in the runtime model receive the scheduler as an argument, allowing them to schedule work or submit new computations.

```python
class ScheduledEffectHandler(Protocol):
    def __call__(
        self,
        effect: EffectBase,
        env: Environment,
        store: Store,
        k: Continuation,
        scheduler: Scheduler,
    ) -> HandlerResult:
        ...
```

### HandlerResult Types

A handler must return one of three result types to indicate how the runtime should proceed:

| Type | Description |
|------|-------------|
| `Resume(value, store)` | Resume the current continuation immediately with the given value. |
| `Suspend(awaitable, store)` | Await an external `asyncio` operation, then resume with its result. |
| `Scheduled(store)` | The continuation was submitted to the scheduler; the runtime should pick the next task. |

## Reference Schedulers

### FIFOScheduler
The simplest scheduler. Continuations are executed in the exact order they were submitted. Ideal for simple sequential processing and unit tests.

### PriorityScheduler
Executes continuations based on a priority value provided in the `hint`. Lower values have higher priority. It uses a sequence counter to ensure deterministic FIFO behavior for equal priorities.

### SimulationScheduler
A discrete event simulation (DES) scheduler. It maintains a "ready" stack (LIFO) for immediate tasks and a "timed" priority queue for future tasks. Time advances only when the ready stack is empty, jumping to the next scheduled event time.

### RealtimeScheduler (Experimental)
Integrates with the `asyncio` event loop. It uses `asyncio.sleep()` for timed delays, allowing `doeff` programs to interact with real wall-clock time.

> **Note**: `RealtimeScheduler` is experimental. When timers are pending but no continuation is immediately ready, the current implementation may not behave as expected. For production use with wall-clock timing, consider using `Suspend` with `asyncio.sleep()` directly.

## Simulation Effects

The module provides built-in effects that take advantage of the scheduler model:

- **`SimDelay(seconds: float)`**: Suspends the program for a specific duration.
- **`SimWaitUntil(target_time: datetime)`**: Suspends until a specific point in time.
- **`SimSubmit(program: Program)`**: Spawns a new concurrent computation in the same scheduler.

## Usage Examples

### Basic Usage

The standard `run()` and `run_sync()` functions now accept an optional `scheduler` parameter:

```python
from doeff import do, Program
from doeff.cesk import run, run_sync
from doeff.runtime import FIFOScheduler, SimulationScheduler

@do
def hello():
    return "Hello, Scheduler!"

# Default: uses FIFOScheduler internally
result = run_sync(hello())

# Explicit scheduler
result = run_sync(hello(), scheduler=SimulationScheduler())
```

For custom schedulers, pass them to `run()` or `run_sync()`:

```python
from doeff.cesk import run
from doeff.runtime import SimulationScheduler

async def main():
    scheduler = SimulationScheduler()
    result = await run(hello(), scheduler=scheduler)
    print(result.value)  # "Hello, Scheduler!"
```

### Simulation vs Realtime

The following trading strategy runs instantly in simulation but follows real time in production:

```python
from datetime import timedelta
from typing import Any
from doeff import do, Program
from doeff.cesk import run
from doeff.runtime import (
    SimDelay, SimulationScheduler, RealtimeScheduler,
    create_sim_delay_handler
)

@do
def trading_strategy() -> Program[str]:
    print("Checking markets...")
    yield SimDelay(seconds=3600)  # Wait 1 hour
    print("Checking markets again...")
    return "Done"

# To run in Simulation:
async def run_sim() -> None:
    sched = SimulationScheduler()
    handlers = {SimDelay: create_sim_delay_handler()}
    await run(trading_strategy(), scheduler=sched, scheduled_handlers=handlers)
    # This completes instantly and current_time advances by 1 hour.

# To run in Realtime:
async def run_realtime() -> None:
    sched = RealtimeScheduler()
    handlers = {SimDelay: create_sim_delay_handler()}
    await run(trading_strategy(), scheduler=sched, scheduled_handlers=handlers)
    # This actually takes 1 hour to complete.
```

### Custom Handlers

Creating a handler that spawns a background worker:

```python
from dataclasses import dataclass
from doeff import Program
from doeff.types import EffectBase
from doeff.cesk import Environment, Store
from doeff.runtime import Resume, Continuation, Scheduler, HandlerResult

@dataclass(frozen=True, kw_only=True)
class SpawnWorker(EffectBase):
    worker_program: Program

    def intercept(self, transform):
        return self

def handle_spawn_worker(
    effect: EffectBase,
    env: Environment,
    store: Store,
    k: Continuation,
    scheduler: Scheduler
) -> HandlerResult:
    assert isinstance(effect, SpawnWorker)
    worker_k = Continuation.from_program(effect.worker_program, env, store)
    scheduler.submit(worker_k)
    return Resume(None, store)
```

## Migration Guide

The legacy `SyncEffectHandler` and `AsyncEffectHandler` protocols have been removed. All effect handlers now use the unified `ScheduledEffectHandler` protocol.

### Writing Handlers

Handlers must use the `ScheduledEffectHandler` signature:

```python
from doeff.runtime import ScheduledEffectHandler, Resume, Suspend, Scheduled

def my_handler(effect, env, store, k, scheduler) -> HandlerResult:
    # For immediate resume:
    return Resume(value, store)
    
    # For async operations:
    return Suspend(awaitable, store)
    
    # For scheduler-managed resumption:
    scheduler.submit(k, hint=some_hint)
    return Scheduled(store)
```

### API Changes

| Old API | New API |
|---------|---------|
| `run(program, pure_handlers=..., effectful_handlers=...)` | `run(program, scheduled_handlers=..., scheduler=...)` |
| `SyncEffectHandler` protocol | `ScheduledEffectHandler` |
| `AsyncEffectHandler` protocol | `ScheduledEffectHandler` |
| `EffectDispatcher` class | `ScheduledEffectDispatcher` |
| `default_pure_handlers()` | `default_scheduled_handlers()` |
| `default_effectful_handlers()` | `default_scheduled_handlers()` |
