# 15. Runtime Scheduler

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

### RealtimeScheduler
Integrates with the `asyncio` event loop. It uses `asyncio.sleep()` for timed delays, allowing `doeff` programs to interact with real wall-clock time.

## Simulation Effects

The module provides built-in effects that take advantage of the scheduler model:

- **`SimDelay(seconds: float)`**: Suspends the program for a specific duration.
- **`SimWaitUntil(target_time: datetime)`**: Suspends until a specific point in time.
- **`SimSubmit(program: Program)`**: Spawns a new concurrent computation in the same scheduler.

## Usage Examples

### Basic Usage

Running a simple program with the `FIFOScheduler`:

```python
from doeff import do, Program
from doeff.runtime import run_with_scheduler, FIFOScheduler, ScheduledHandlerRegistry

@do
def hello():
    return "Hello, Scheduler!"

async def main():
    scheduler = FIFOScheduler()
    registry = ScheduledHandlerRegistry()
    
    result = await run_with_scheduler(hello(), scheduler, registry)
    print(result) # "Hello, Scheduler!"
```

### Simulation vs Realtime

The following trading strategy runs instantly in simulation but follows real time in production:

```python
from datetime import timedelta
from typing import Any
from doeff import do, Program
from doeff.runtime import (
    SimDelay, SimulationScheduler, RealtimeScheduler, 
    run_with_scheduler, ScheduledHandlerRegistry,
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
    reg = ScheduledHandlerRegistry({SimDelay: create_sim_delay_handler()})
    await run_with_scheduler(trading_strategy(), sched, reg)
    # This completes instantly and current_time advances by 1 hour.

# To run in Realtime:
async def run_realtime() -> None:
    sched = RealtimeScheduler()
    reg = ScheduledHandlerRegistry({SimDelay: create_sim_delay_handler()})
    await run_with_scheduler(trading_strategy(), sched, reg)
    # This actually takes 1 hour to complete.
```

### Custom Handlers

Creating a handler that spawns a background worker:

```python
from dataclasses import dataclass
from typing import Any
from doeff import Program, Effect
from doeff.types import Environment, Store, EffectBase
from doeff.runtime import ScheduledEffectHandler, Resume, Continuation, Scheduler, HandlerResult

@dataclass(frozen=True)
class SpawnWorker(EffectBase):
    worker_program: Program

    def intercept(self, transform):
        return self # Simple effects don't always need complex interception

def handle_spawn_worker(
    effect: Effect,
    env: Environment,
    store: Store,
    k: Continuation,
    scheduler: Scheduler
) -> HandlerResult:
    assert isinstance(effect, SpawnWorker)
    
    # Create a new continuation for the worker
    worker_k = Continuation.from_program(effect.worker_program, env, store)
    
    # Submit worker to scheduler (hint=None for immediate execution)
    scheduler.submit(worker_k)
    
    # Resume the original caller immediately
    return Resume(None, store)
```

## Migration Guide

If you have existing handlers designed for the `ProgramInterpreter`, you can adapt them to the new `ScheduledEffectHandler` protocol using utility functions:

### Synchronous Handlers
Use `adapt_pure_handler` for handlers that don't need async or scheduling:

```python
from doeff.runtime import adapt_pure_handler

def my_old_sync_handler(effect, env, store):
    return "result", store

new_handler = adapt_pure_handler(my_old_sync_handler)
```

### Asynchronous Handlers
Use `adapt_async_handler` for standard async handlers:

```python
from doeff.runtime import adapt_async_handler

async def my_old_async_handler(effect, env, store):
    await asyncio.sleep(1)
    return "result", store

new_handler = adapt_async_handler(my_old_async_handler)
```

These adapters wrap the old logic and return `Resume` or `Suspend` respectively, ensuring compatibility with the scheduler-based loop.
