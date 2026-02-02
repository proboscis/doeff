# CESK Handler API Reference

This document provides comprehensive documentation for implementing custom effect handlers in the doeff framework. The CESK machine (Control, Environment, Store, Kontinuation) is the interpreter core that executes effect programs.

## Table of Contents

- [Overview](#overview)
- [Handler Signature](#handler-signature)
- [FrameResult Types](#frameresult-types)
- [Request Types](#request-types)
- [State Types](#state-types)
  - [TaskState](#taskstate)
  - [Store](#store)
  - [Environment](#environment)
- [Registering Custom Handlers](#registering-custom-handlers)
- [Examples](#examples)
  - [Simple Sync Handler](#simple-sync-handler)
  - [State-Modifying Handler](#state-modifying-handler)
  - [Sub-program Handler](#sub-program-handler)
  - [Error-Producing Handler](#error-producing-handler)
- [Migration Guide](#migration-guide)
- [Best Practices](#best-practices)

---

## Overview

Effect handlers in doeff are functions that process specific effect types and return a `FrameResult` indicating how execution should continue. Handlers are registered by effect type and invoked by the CESK interpreter when that effect is encountered.

```python
from doeff.cesk import default_handlers, ContinueValue
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store

def handle_my_effect(
    effect: MyEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    # Process the effect
    result = do_something(effect.data)
    
    # Return continuation
    return ContinueValue(
        value=result,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )
```

---

## Handler Signature

Every effect handler follows this signature:

```python
def handle_xxx(
    effect: EffectType,
    task_state: TaskState,
    store: Store,
) -> FrameResult
```

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `effect` | `EffectType` | The effect instance being handled |
| `task_state` | `TaskState` | Current task's execution state (env, kontinuation, control, status) |
| `store` | `Store` | Shared mutable state dictionary across all tasks |

### Return Value

Handlers must return a `FrameResult` - one of:

- `ContinueValue` - Continue with a computed value
- `ContinueError` - Propagate an error
- `ContinueProgram` - Evaluate a sub-program
- `ContinueGenerator` - Resume a suspended generator

---

## FrameResult Types

Import from `doeff.cesk.frames`:

```python
from doeff.cesk.frames import (
    FrameResult,
    ContinueValue,
    ContinueError,
    ContinueProgram,
    ContinueGenerator,
)
```

### ContinueValue

Return an immediate value and continue execution.

```python
@dataclass(frozen=True)
class ContinueValue:
    value: Any           # The result value
    env: Environment     # Environment to use (usually task_state.env)
    store: Store         # Updated store (can be modified)
    k: Kontinuation      # Continuation stack (usually task_state.kontinuation)
```

**Usage:**
```python
def handle_get_config(effect: GetConfigEffect, task_state: TaskState, store: Store) -> FrameResult:
    config_value = store.get("config", {}).get(effect.key)
    return ContinueValue(
        value=config_value,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )
```

### ContinueError

Propagate an error through the continuation stack.

```python
@dataclass(frozen=True)
class ContinueError:
    error: BaseException         # The exception to propagate
    env: Environment             # Environment to use
    store: Store                 # Store at error point
    k: Kontinuation              # Continuation stack
    captured_traceback: Any | None = None  # Optional traceback capture
```

**Usage:**
```python
def handle_require(effect: RequireEffect, task_state: TaskState, store: Store) -> FrameResult:
    if effect.key not in store:
        return ContinueError(
            error=KeyError(f"Required key not found: {effect.key!r}"),
            env=task_state.env,
            store=store,
            k=task_state.kontinuation,
        )
    return ContinueValue(
        value=store[effect.key],
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )
```

### ContinueProgram

Schedule a sub-program for evaluation. The result of the sub-program will be the handler's result.

```python
@dataclass(frozen=True)
class ContinueProgram:
    program: ProgramLike   # The program to evaluate
    env: Environment       # Environment for the program
    store: Store           # Store to use
    k: Kontinuation        # Continuation stack (can add frames)
```

**Usage:**
```python
def handle_local(effect: LocalEffect, task_state: TaskState, store: Store) -> FrameResult:
    # Create new environment with updates
    new_env = task_state.env | FrozenDict(effect.env_update)
    
    # Push frame to restore original env after sub-program completes
    return ContinueProgram(
        program=effect.sub_program,
        env=new_env,
        store=store,
        k=[LocalFrame(task_state.env)] + task_state.kontinuation,
    )
```

### ContinueGenerator

Resume a suspended generator (internal use, typically for ReturnFrame).

```python
@dataclass(frozen=True)
class ContinueGenerator:
    generator: Generator[Any, Any, Any]  # The generator to resume
    send_value: Any | None               # Value to send (if not throwing)
    throw_error: BaseException | None    # Error to throw (if not sending)
    env: Environment                     # Environment to use
    store: Store                         # Store to use
    k: Kontinuation                      # Continuation stack
    program_call: KleisliProgramCall | None = None  # Optional call info
```

---

## Request Types

Request types are used when an effect requires runtime-level coordination (async I/O, task spawning, etc.). Import from `doeff.cesk.state`:

```python
from doeff.cesk.state import (
    Request,
    CreateTask,
    CreateFuture,
    ResolveFuture,
    PerformIO,
    AwaitExternal,
    CreateSpawn,
)
```

| Type | Description | Usage |
|------|-------------|-------|
| `PerformIO` | Execute a synchronous side-effectful action | `PerformIO(action=lambda: read_file())` |
| `AwaitExternal` | Wait for an external awaitable (asyncio integration) | `AwaitExternal(awaitable=coro)` |
| `CreateTask` | Spawn a new concurrent task from a program | `CreateTask(program=sub_program)` |
| `CreateFuture` | Create a new unresolved future | `CreateFuture()` |
| `ResolveFuture` | Resolve a future with a value | `ResolveFuture(future_id=fid, value=42)` |
| `CreateSpawn` | Spawn a program on an external backend | `CreateSpawn(program=p, backend=backend)` |

### Note on Request Handling

Most effects return `FrameResult` directly. Request types are typically handled by the **runtime** (AsyncRuntime, SyncRuntime), not by effect handlers. If your handler needs async coordination, the runtime intercepts specific effect types before handler dispatch.

---

## State Types

### TaskState

Per-task CESK execution state. Import from `doeff.cesk.state`:

```python
from doeff.cesk.state import TaskState

@dataclass
class TaskState:
    control: Control           # Current computation state
    env: Environment           # Immutable reader context (FrozenDict)
    kontinuation: Kontinuation # Call stack (list of Frames)
    status: TaskStatus         # Ready, Blocked, Requesting, or Done
```

**Key Properties:**

| Property | Type | Description |
|----------|------|-------------|
| `control` | `Control` | Current state: `Value`, `Error`, `EffectControl`, or `ProgramControl` |
| `env` | `Environment` | Immutable key-value bindings (provided via `Ask` effect) |
| `kontinuation` | `Kontinuation` | Stack of continuation frames |
| `status` | `TaskStatus` | Task execution status |

**TaskStatus Types:**

```python
from doeff.cesk.state import Ready, Blocked, Requesting, Done as TaskDone

Ready(resume_value=None)     # Task ready to run
Blocked(condition=...)       # Waiting for condition (TimeCondition, FutureCondition, etc.)
Requesting(request=...)      # Needs runtime operation
TaskDone(result=Ok(value))   # Completed successfully
TaskDone(result=Err(error))  # Completed with error
```

### Store

Shared mutable state dictionary across all tasks. Type alias for `dict[str, Any]`:

```python
from doeff.cesk.types import Store

store: Store = {}
```

**Reserved Keys:**

| Key | Description |
|-----|-------------|
| `__log__` | Accumulated `Tell` messages (list) |
| `__memo__` | Memoization cache |
| `__cache_storage__` | Cache effect storage |
| `__dispatcher__` | Event dispatcher |
| `__current_time__` | Simulated current time (SimulationRuntime) |
| `__graph__` | Graph tracking data |
| `__ask_lazy_cache__` | Lazy `Ask` evaluation cache |

### Environment

Immutable mapping for reader-style context. Type alias for `FrozenDict[Any, Any]`:

```python
from doeff.cesk.types import Environment, empty_environment
from doeff._vendor import FrozenDict

env: Environment = FrozenDict({"db": database, "config": config})

# Extend environment (returns new FrozenDict)
new_env = env | FrozenDict({"extra": value})
```

---

## Registering Custom Handlers

Extend the default handler registry with your custom handlers:

```python
from doeff.cesk.handlers import default_handlers

# Get the default handler mapping
handlers = default_handlers()

# Register your custom handler
handlers[MyCustomEffect] = handle_my_custom_effect

# Use custom handlers with sync_run
from doeff import sync_run

# Create handler preset with custom handler
custom_preset = list(handlers.values())
result = sync_run(my_program(), custom_preset)
```

### Handler Registry Structure

The handler registry is a `dict[type, Handler]` mapping effect types to handler functions:

```python
{
    PureEffect: handle_pure,
    AskEffect: handle_ask,
    StateGetEffect: handle_state_get,
    StatePutEffect: handle_state_put,
    # ... your handlers
    MyCustomEffect: handle_my_custom,
}
```

---

## Examples

### Simple Sync Handler

A handler that returns an immediate value without modifying state:

```python
from dataclasses import dataclass
from doeff._types_internal import EffectBase
from doeff.cesk.frames import ContinueValue, FrameResult
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store


@dataclass(frozen=True)
class GetEnvVar(EffectBase):
    """Effect to get an environment variable."""
    name: str


def handle_get_env_var(
    effect: GetEnvVar,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    import os
    value = os.environ.get(effect.name)
    return ContinueValue(
        value=value,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )
```

### State-Modifying Handler

A handler that updates the shared store:

```python
from dataclasses import dataclass
from doeff._types_internal import EffectBase
from doeff.cesk.frames import ContinueValue, FrameResult
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store


@dataclass(frozen=True)
class IncrementCounter(EffectBase):
    """Effect to increment a named counter."""
    name: str
    amount: int = 1


def handle_increment_counter(
    effect: IncrementCounter,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    counters = store.get("__counters__", {})
    current = counters.get(effect.name, 0)
    new_value = current + effect.amount
    
    # Create new store with updated counters
    new_counters = {**counters, effect.name: new_value}
    new_store = {**store, "__counters__": new_counters}
    
    return ContinueValue(
        value=new_value,
        env=task_state.env,
        store=new_store,
        k=task_state.kontinuation,
    )
```

### Sub-program Handler

A handler that executes a sub-program and transforms its result:

```python
from dataclasses import dataclass
from typing import Callable, Any
from doeff._types_internal import EffectBase
from doeff.cesk.frames import (
    ContinueProgram,
    ContinueValue,
    FrameResult,
    Frame,
    Kontinuation,
)
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store, Environment
from doeff.effects._program_types import ProgramLike


@dataclass(frozen=True)
class WithTimeout(EffectBase):
    """Run a sub-program with a timeout wrapper."""
    program: ProgramLike
    timeout_seconds: float


@dataclass(frozen=True)
class TimeoutFrame:
    """Frame to track timeout context."""
    timeout_seconds: float
    saved_env: Environment
    
    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        # Sub-program completed, wrap result
        return ContinueValue(
            value={"status": "completed", "value": value},
            env=self.saved_env,
            store=store,
            k=k_rest,
        )
    
    def on_error(
        self,
        error: BaseException,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        from doeff.cesk.frames import ContinueError
        return ContinueError(
            error=error,
            env=self.saved_env,
            store=store,
            k=k_rest,
        )


def handle_with_timeout(
    effect: WithTimeout,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    # Push a timeout tracking frame and execute sub-program
    return ContinueProgram(
        program=effect.program,
        env=task_state.env,
        store=store,
        k=[TimeoutFrame(effect.timeout_seconds, task_state.env)] + task_state.kontinuation,
    )
```

### Error-Producing Handler

A handler that may produce errors:

```python
from dataclasses import dataclass
from doeff._types_internal import EffectBase
from doeff.cesk.frames import ContinueValue, ContinueError, FrameResult
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store


@dataclass(frozen=True)
class DivideEffect(EffectBase):
    """Effect to perform division."""
    numerator: float
    denominator: float


def handle_divide(
    effect: DivideEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    if effect.denominator == 0:
        return ContinueError(
            error=ZeroDivisionError("Cannot divide by zero"),
            env=task_state.env,
            store=store,
            k=task_state.kontinuation,
        )
    
    result = effect.numerator / effect.denominator
    return ContinueValue(
        value=result,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )
```

---

## Migration Guide

### Handler Signature Changes

The handler signature was simplified to use `TaskState` instead of separate parameters:

| Old Signature | New Signature |
|---------------|---------------|
| `def handler(effect, env, store, k, scheduler)` | `def handler(effect, task_state, store)` |

**Before (deprecated):**
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

### FrameResult Type Changes

| Old Type | New Type |
|----------|----------|
| `Resume(value, store)` | `ContinueValue(value, env, store, k)` |
| `Schedule(program, env, store, k)` | `ContinueProgram(program, env, store, k)` |
| `AwaitPayload(coro)` | Use `PerformIO` or `AwaitExternal` request |
| `HandlerResult` | `FrameResult` |

### Import Changes

| Old Import | New Import |
|------------|------------|
| `from doeff.runtime import ...` | `from doeff.cesk import ...` |
| `from doeff.interpreter import ...` | `from doeff.cesk import ...` |
| `from doeff.handler_result import Resume` | `from doeff.cesk.frames import ContinueValue` |

### Complete Migration Example

**Before:**
```python
from doeff.runtime import Resume, HandlerResult
from doeff.types import EffectBase

class MyEffect(EffectBase):
    value: int

def handle_my_effect(effect, env, store, k, scheduler) -> HandlerResult:
    result = effect.value * 2
    return Resume(result, store)
```

**After:**
```python
from dataclasses import dataclass
from doeff._types_internal import EffectBase
from doeff.cesk.frames import ContinueValue, FrameResult
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store


@dataclass(frozen=True)
class MyEffect(EffectBase):
    value: int


def handle_my_effect(
    effect: MyEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    result = effect.value * 2
    return ContinueValue(
        value=result,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )
```

### Migration Checklist

- [ ] Update imports from `doeff.runtime` to `doeff.cesk`
- [ ] Change handler signature from `(effect, env, store, k, scheduler)` to `(effect, task_state, store)`
- [ ] Replace `Resume(value, store)` with `ContinueValue(value, env, store, k)`
- [ ] Replace `Schedule(...)` with `ContinueProgram(...)`
- [ ] Access `env` via `task_state.env` instead of parameter
- [ ] Access `k` (kontinuation) via `task_state.kontinuation` instead of parameter
- [ ] Update effect base class import to `from doeff._types_internal import EffectBase`
- [ ] Register handlers using `default_handlers()` dictionary
- [ ] Test handlers with the new runtime classes (SyncRuntime, AsyncRuntime, SimulationRuntime)

---

## Best Practices

### 1. Keep Handlers Pure When Possible

Handlers should ideally be deterministic functions of their inputs. Side effects should be modeled as effects themselves:

```python
# Good: Return computed value
def handle_add(effect, task_state, store):
    return ContinueValue(
        value=effect.a + effect.b,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )

# Avoid: Side effects in handler
def handle_add_bad(effect, task_state, store):
    print(f"Adding {effect.a} + {effect.b}")  # Side effect!
    return ContinueValue(...)
```

### 2. Don't Mutate Store In-Place

Always create a new store dictionary when modifying:

```python
# Good: Create new store
new_store = {**store, "key": new_value}
return ContinueValue(..., store=new_store, ...)

# Bad: Mutate in place
store["key"] = new_value  # Don't do this!
return ContinueValue(..., store=store, ...)
```

### 3. Preserve Environment Unless Intentionally Changing Scope

Most handlers should pass through `task_state.env` unchanged:

```python
return ContinueValue(
    value=result,
    env=task_state.env,  # Preserve environment
    store=store,
    k=task_state.kontinuation,
)
```

Only modify `env` for scoping effects like `Local`:

```python
new_env = task_state.env | FrozenDict({"new_binding": value})
return ContinueProgram(
    program=sub_program,
    env=new_env,  # Modified environment for sub-program
    ...
)
```

### 4. Use Custom Frames for Complex Control Flow

When your handler needs to intercept the result of a sub-program, define a custom Frame:

```python
@dataclass(frozen=True)
class MyCustomFrame:
    saved_env: Environment
    
    def on_value(self, value, env, store, k_rest) -> FrameResult:
        # Transform or wrap the sub-program's result
        return ContinueValue(
            value=transform(value),
            env=self.saved_env,
            store=store,
            k=k_rest,
        )
    
    def on_error(self, error, env, store, k_rest) -> FrameResult:
        # Handle or propagate errors
        return ContinueError(
            error=error,
            env=self.saved_env,
            store=store,
            k=k_rest,
        )
```

### 5. Type Your Handlers

Use proper type annotations for better IDE support and documentation:

```python
from doeff.cesk.frames import FrameResult
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store

def handle_my_effect(
    effect: MyEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    ...
```

---

## See Also

- [Runtime Variants](20-runtime-scheduler.md) - SyncRuntime, AsyncRuntime, SimulationRuntime
- [Effects Matrix](21-effects-matrix.md) - Complete effect support reference
- [Error Handling](05-error-handling.md) - Safe effect and error propagation
- [Core Concepts](02-core-concepts.md) - Programs and Effects overview
