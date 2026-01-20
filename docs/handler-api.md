# CESK Handler API

This document describes the CESK Handler API, which allows subpackage authors to implement custom effect handlers for the `doeff` effect system.

## 1. Introduction

The CESK machine (Control, Environment, Store, Kontinuation) is the core execution engine of `doeff`. When a `Program` yields an `Effect`, the runtime looks up a corresponding **Handler** to process that effect.

The Handler API provides a unified way to:
- Process pure values and side effects
- Manipulate the environment (reader context)
- Access and modify shared mutable state (store)
- Manage the continuation stack (call stack)
- Spawn new tasks or perform asynchronous I/O

## 2. Handler Signature

Every handler must follow this exact signature:

```python
from doeff.cesk.frames import FrameResult
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store

def handle_my_effect(
    effect: MyEffectType,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    """Handle a specific effect type."""
    # ... implementation ...
```

### Parameters

- **`effect`**: The effect instance being handled. This is the object yielded by the `Program`.
- **`task_state`**: An instance of `TaskState` containing the current task's execution context:
    - `task_state.env`: The current `Environment` (immutable FrozenDict).
    - `task_state.kontinuation`: The current `Kontinuation` stack.
- **`store`**: The global `Store` (dict[str, Any]). This is mutable and shared across all tasks.

### Return Value

Handlers must return a `FrameResult`, which instructs the CESK machine on how to proceed.

## 3. FrameResult Types

Handlers return one of the following types from `doeff.cesk.frames` to control the machine's next step:

| Type | Fields | Usage |
|------|--------|-------|
| `ContinueValue` | `value`, `env`, `store`, `k` | Return an immediate value to the current continuation. |
| `ContinueProgram` | `program`, `env`, `store`, `k` | Evaluate a sub-program and push it onto the stack. |
| `ContinueError` | `error`, `env`, `store`, `k` | Propagate an exception through the continuation stack. |
| `ContinueGenerator` | `generator`, `send_value`, `throw_error`, `env`, `store`, `k`, `program_call` | Continue execution of an existing generator. |

### Detailed Signatures

```python
@dataclass(frozen=True)
class ContinueValue:
    value: Any
    env: Environment
    store: Store
    k: Kontinuation

@dataclass(frozen=True)
class ContinueProgram:
    program: ProgramLike
    env: Environment
    store: Store
    k: Kontinuation

@dataclass(frozen=True)
class ContinueError:
    error: BaseException
    env: Environment
    store: Store
    k: Kontinuation

@dataclass(frozen=True)
class ContinueGenerator:
    generator: Generator[Any, Any, Any]
    send_value: Any | None
    throw_error: BaseException | None
    env: Environment
    store: Store
    k: Kontinuation
    program_call: KleisliProgramCall | None = None
```

## 4. Request Types

For operations that require runtime-level coordination (like async I/O or concurrency), handlers can return a `ContinueValue` where the value is a **Request**. These are defined in `doeff.cesk.state`.

| Type | Usage | Example |
|------|-------|---------|
| `PerformIO(action)` | Execute a synchronous callable as a side-effect. | Writing to a file, calling `print()`. |
| `AwaitExternal(awaitable)` | Await an `asyncio` awaitable. | HTTP requests using `httpx`. |
| `CreateTask(program)` | Spawn a new concurrent task in the current runtime. | Parallel execution. |
| `CreateFuture()` | Create a new unresolved future handle. | Manual coordination. |
| `ResolveFuture(future_id, value)` | Signal completion of a future. | Unblocking a waiting task. |
| `CreateSpawn(program, backend)` | Spawn a program on an external backend. | Subprocesses or thread pools. |

## 5. Task and Store State

### TaskState (`doeff.cesk.state.TaskState`)

Encapsulates the state of a single task:
- **`control`**: Current computation state (Value, Error, Effect, or Program).
- **`env`**: `Environment` - Immutable reader context (`FrozenDict`).
- **`kontinuation`**: `Kontinuation` - The stack of frames to be executed.
- **`status`**: `TaskStatus` - `Ready`, `Blocked`, `Requesting`, or `Done`.

### Store (`doeff.cesk.types.Store`)

- **Type**: `dict[str, Any]`
- Shared mutable state across all tasks within the same runtime execution.
- **Reserved Keys**:
    - `__log__`: Used by the writer effect system.
    - `__cache_storage__`: Used by the cache system.
    - `__ask_lazy_cache__`: Used for lazy `Ask` evaluation.
    - `__graph__`: Used for execution graph tracking.
    - `__current_time__`: Used for time-related effects in simulation.

### Environment (`doeff.cesk.types.Environment`)

- **Type**: `FrozenDict[Any, Any]`
- Immutable mapping used for reader-like context.
- Uses copy-on-write semantics (e.g., `new_env = env | FrozenDict({key: val})`).

## 6. Registering Custom Handlers

To use your custom handlers, extend the dictionary returned by `default_handlers()` and pass it to a runtime.

```python
from doeff.cesk import default_handlers, SyncRuntime
from my_package import MyEffect, handle_my_effect

def my_custom_handlers():
    # Start with standard handlers
    handlers = default_handlers()
    # Register your custom effect handler
    handlers[MyEffect] = handle_my_effect
    return handlers

# Initialize runtime with custom handlers
runtime = SyncRuntime(handlers=my_custom_handlers())

# Run your program
result = runtime.run(my_program())
```

## 7. Examples

### Example 1: Simple Sync Handler
A handler that returns a value immediately without changing the environment or stack.

```python
from doeff.cesk.frames import ContinueValue, FrameResult
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store
from doeff.effects.pure import PureEffect

def handle_pure(
    effect: PureEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    return ContinueValue(
        value=effect.value,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )
```

### Example 2: Handler that Pushes Frames
A handler that updates the environment and pushes a new frame onto the continuation stack before evaluating a sub-program.

```python
from doeff.cesk.frames import ContinueProgram, FrameResult, LocalFrame
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store
from doeff._vendor import FrozenDict
from doeff.effects.reader import LocalEffect

def handle_local(
    effect: LocalEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    # Update environment
    new_env = task_state.env | FrozenDict(effect.env_update)
    
    # Push LocalFrame to restore environment after sub_program completes
    new_k = [LocalFrame(task_state.env)] + task_state.kontinuation
    
    return ContinueProgram(
        program=effect.sub_program,
        env=new_env,
        store=store,
        k=new_k,
    )
```

### Example 3: Handler with Error Handling
A handler that performs an operation and handles potential exceptions by returning `ContinueError`.

```python
from doeff.cesk.frames import ContinueValue, ContinueError, FrameResult
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store
from doeff.effects.io import IOPerformEffect

def handle_io(
    effect: IOPerformEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    try:
        # Perform the I/O action
        result = effect.action()
        return ContinueValue(
            value=result,
            env=task_state.env,
            store=store,
            k=task_state.kontinuation,
        )
    except Exception as ex:
        # Propagate the error through the continuation stack
        return ContinueError(
            error=ex,
            env=task_state.env,
            store=store,
            k=task_state.kontinuation,
        )
```

## 8. Migration Guide

The CESK machine introduces a more robust and unified API compared to previous versions of the `doeff` runtime.

### API Mapping

| Old API | New API | Notes |
|---------|---------|-------|
| `Resume(value, store)` | `ContinueValue(value, env, store, k)` | Must explicitly pass `env` and `k` from `task_state`. |
| `Schedule(AwaitPayload(...), store)` | `ContinueProgram(...)` or use Request types | Use `PerformIO` for synchronous I/O. |
| `AwaitPayload(coro)` | `PerformIO(action)` or `AwaitExternal(awaitable)` | Selection depends on sync vs async nature. |
| `HandlerResult` | `FrameResult` | The type alias has been renamed for clarity. |
| `from doeff.runtime import ...` | `from doeff.cesk import ...` | Core runtime types moved to the `cesk` package. |
| Signature `(effect, env, store)` | `(effect, task_state, store)` | `env` is now accessed via `task_state.env`. |

### Migration Checklist

- [ ] Update imports from `doeff.runtime` to `doeff.cesk`.
- [ ] Change handler signatures to accept `TaskState` instead of `env`.
- [ ] Ensure handlers return `FrameResult` types (e.g., `ContinueValue`) instead of the old `HandlerResult`.
- [ ] Update any `Resume(...)` calls to `ContinueValue(...)`, passing `task_state.env` and `task_state.kontinuation`.
- [ ] Replace `AwaitPayload` usage with appropriate `Request` types (`PerformIO` or `AwaitExternal`).
- [ ] Verify your handlers with `SyncRuntime` or `AsyncRuntime`.
