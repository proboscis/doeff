# Effects, Handlers, and Runtime Support Matrix

This document provides a comprehensive overview of all effects defined in doeff, their handler implementations, and behavior with different handler presets.

**Architecture Note:** `run` and `async_run` are steppers that step through the CESK machine. The actual effect handling and scheduling is done by handlers. The columns below show behavior when using the corresponding handler preset.

## Quick Reference

| Status | Meaning |
|--------|---------|
| Supported | Handler processes effect normally |
| Cooperative | `task_scheduler_handler` manages via cooperative scheduling |
| Thread-based | `sync_await_handler` runs awaitable in background thread |
| Async | Handler produces async escape for `async_run` to await |

## Core Effects (Reader/State/Writer)

| Effect | Constructor | Handler | default_handlers() | default_handlers() | Tested |
|--------|-------------|---------|----------------------|------------------------|--------|
| `AskEffect` | `Ask(key)` | `core_handler` | Supported | Supported | Yes |
| `LocalEffect` | `Local(env, program)` | `core_handler` | Supported | Supported | Yes |
| `StateGetEffect` | `Get(key)` | `core_handler` | Supported | Supported | Yes |
| `StatePutEffect` | `Put(key, value)` | `core_handler` | Supported | Supported | Yes |
| `StateModifyEffect` | `Modify(key, func)` | `core_handler` | Supported | Supported | Yes |
| `WriterTellEffect` | `Tell(msg)` / `Log(msg)` | `core_handler` | Supported | Supported | Yes |
| `WriterListenEffect` | `Listen(program)` | `core_handler` | Supported | Supported | Yes |

### Ask Lazy Program Evaluation

When an environment value is a `Program`, it is evaluated lazily on first `Ask` access. The result is cached for subsequent accesses within the same execution:

```python
@do
def use_config():
    # If env["config"] is a Program, it runs lazily here
    config = yield Ask("config")
    return config

# Pass a Program as env value - evaluated lazily on first Ask
result = run(
    use_config(),
    default_handlers(),
    env={"config": expensive_computation()}  # Program, not value
)
```

See [SPEC-EFF-001](../specs/effects/SPEC-EFF-001-reader.md) for details.

## Control Flow Effects

| Effect | Constructor | Handler | default_handlers() | default_handlers() | Tested |
|--------|-------------|---------|----------------------|------------------------|--------|
| `PureEffect` | `Pure(value)` | `core_handler` | Supported | Supported | Yes |
| `ResultSafeEffect` | `Safe(program)` | `core_handler` | Supported | Supported | Yes |
| `InterceptEffect` | `intercept_program_effect()` | `core_handler` | Supported | Supported | Yes |

## IO Effects

| Effect | Constructor | Handler | default_handlers() | default_handlers() | Tested |
|--------|-------------|---------|----------------------|------------------------|--------|
| `IOPerformEffect` | `IO(action)` | `core_handler` | Supported | Supported | Yes |

## Cache Effects

| Effect | Constructor | Handler | default_handlers() | default_handlers() | Tested |
|--------|-------------|---------|----------------------|------------------------|--------|
| `CacheGetEffect` | `CacheGet(key)` | `core_handler` | Supported | Supported | Yes |
| `CachePutEffect` | `CachePut(key, value)` | `core_handler` | Supported | Supported | Yes |
| `CacheExistsEffect` | `CacheExists(key)` | `core_handler` | Supported | Supported | Yes |
| `CacheDeleteEffect` | `CacheDelete(key)` | `core_handler` | Supported | Supported | Yes |

## Time Effects

| Effect | Constructor | Handler | default_handlers() | default_handlers() | Tested |
|--------|-------------|---------|----------------------|------------------------|--------|
| `DelayEffect` | `Delay(seconds)` | `core_handler` | Real `time.sleep` | Async `asyncio.sleep` | Yes |
| `GetTimeEffect` | `GetTime()` | `core_handler` | Supported | Supported | Yes |
| `WaitUntilEffect` | `WaitUntil(datetime)` | `core_handler` | Real wait | Async wait | Yes |

### Time Effect Handling Note

- **default_handlers()**: `core_handler` uses real wall-clock time
- **default_handlers()**: Handler produces async escape, `async_run` awaits it

## Concurrency Effects

| Effect | Constructor | Handler | default_handlers() | default_handlers() | Tested |
|--------|-------------|---------|----------------------|------------------------|--------|
| `SpawnEffect` | `Spawn(program)` | `task_scheduler_handler` | Cooperative | asyncio.create_task | Yes |
| `WaitEffect` | `Wait(future)` | `task_scheduler_handler` | Cooperative | Async | Yes |
| `GatherEffect` | `Gather(*futures)` | `task_scheduler_handler` | Cooperative | Parallel | Yes |
| `RaceEffect` | `Race(*futures)` | `task_scheduler_handler` | Cooperative | Parallel | Yes |
| `TaskCancelEffect` | `task.cancel()` | `task_scheduler_handler` | Cooperative | Async | Yes |
| `FutureAwaitEffect` | `Await(awaitable)` | `sync_await_handler` / `python_async_syntax_escape_handler` | Thread-based | Async | Yes |

### Cooperative Scheduling (task_scheduler_handler)

The `task_scheduler_handler` implements concurrency via **cooperative scheduling**:

- **Single-threaded**: All task code runs on one thread (no races in user code)
- **Interleaved execution**: Tasks yield control at every `yield` statement
- **Deterministic**: Execution order is reproducible for pure compute
- **Timer threads**: `Delay` uses background threads for timing

### Spawn/Wait/Gather Usage

The `Spawn` effect creates tasks with **snapshot semantics**:
- Environment and store are snapshotted at spawn time
- Spawned tasks run with isolated state (no mutation of parent's store)
- Use `Wait(task)` to retrieve result
- Use `task.cancel()` to request cancellation

```python
@do
def concurrent_work():
    # Spawn tasks (returns immediately)
    t1 = yield Spawn(compute_a())
    t2 = yield Spawn(compute_b())
    t3 = yield Spawn(compute_c())
    
    # Wait for single task
    result_a = yield Wait(t1)
    
    # Or gather all at once
    results = yield Gather(t2, t3)
    return (result_a, results)
```

See [SPEC-EFF-005](../specs/effects/SPEC-EFF-005-concurrency.md) for details.

### Concurrency Model Comparison

| Aspect | default_handlers() | default_handlers() |
|--------|----------------------|----------------------|
| `Spawn` | asyncio.create_task | Enqueue to `task_scheduler_handler` |
| `Gather` | asyncio.gather (true parallel) | Cooperative interleaving |
| Parallelism | Yes (kernel I/O multiplexing) | No (single-threaded) |
| Deterministic | No | Yes |
| `Await` support | Direct await | Thread-based via `sync_await_handler` |

## Atomic Effects

| Effect | Constructor | Handler | default_handlers() | default_handlers() | Tested |
|--------|-------------|---------|----------------------|------------------------|--------|
| `AtomicGetEffect` | `AtomicGet(key)` | `core_handler` | Supported | Supported | Yes |
| `AtomicUpdateEffect` | `AtomicUpdate(key, updater)` | `core_handler` | Supported | Supported | Yes |

## Debug/Introspection Effects

| Effect | Constructor | Handler | default_handlers() | default_handlers() | Tested |
|--------|-------------|---------|----------------------|------------------------|--------|
| `ProgramCallFrameEffect` | `ProgramCallFrame(depth)` | `core_handler` | Supported | Supported | Yes |
| `ProgramCallStackEffect` | `ProgramCallStack()` | `core_handler` | Supported | Supported | Yes |

## Graph Effects

| Effect | Constructor | Handler | default_handlers() | default_handlers() | Tested |
|--------|-------------|---------|----------------------|------------------------|--------|
| `GraphStepEffect` | `Step(value, meta)` | `core_handler` | Supported | Supported | Yes |
| `GraphAnnotateEffect` | `Annotate(meta)` | `core_handler` | Supported | Supported | Yes |
| `GraphSnapshotEffect` | `Snapshot()` | `core_handler` | Supported | Supported | Yes |
| `GraphCaptureEffect` | `CaptureGraph(program)` | `core_handler` | Supported | Supported | Yes |

## Stepper and Handler Preset Overview

| Stepper | Handler Preset | Key Handlers |
|---------|----------------|--------------|
| `run` | `default_handlers()` | `core_handler`, `task_scheduler_handler`, `sync_await_handler` |
| `async_run` | `default_handlers()` | `core_handler`, `task_scheduler_handler`, `python_async_syntax_escape_handler` |

**Note:** The steppers (`run`, `async_run`) just step through the CESK machine. All effect handling and scheduling logic is in the handlers.

### Selection Guide

| Use Case | Recommended |
|----------|-------------|
| Production async code | `async_run` |
| True parallel I/O | `async_run` |
| Python coroutine interop (`Await`) | `async_run` |
| Scripts, CLI tools | `run` |
| Concurrent tasks without asyncio | `run` |
| Testing with controlled time | `run` with `__current_time__` in store |
| Deterministic concurrent testing | `run` |

## Handler Registration

Default handlers are available via preset lists:

```python
from doeff import default_handlers, default_handlers

# Use presets directly with run/async_run
result = run(my_program(), default_handlers())
```

Custom handlers can be prepended to presets:

```python
from doeff import do, run, default_handlers
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.state import CESKState

@do
def my_handler(effect, ctx: HandlerContext):
    if isinstance(effect, MyEffect):
        return CESKState.with_value(handle_my_effect(effect), ctx.env, ctx.store, ctx.k)
    # Forward unhandled effects
    result = yield effect
    return result

# Prepend custom handler to preset
handlers = [my_handler, *default_handlers()]
result = run(my_program(), handlers)
```

## RuntimeResult

Both `run()` and `arun()` return `RuntimeResult[T]`:

```python
from doeff import run, default_handlers

result = run(my_program(), default_handlers())

if result.is_ok():
    print(f"Success: {result.value}")
else:
    print(f"Error: {result.error}")
    # Access stack traces for debugging:
    # - result.k_stack: Continuation stack
    # - result.effect_stack: Effect call tree
    # - result.python_stack: Python traceback
```

See [SPEC-CESK-002](../specs/cesk-architecture/SPEC-CESK-002-runtime-result.md) for details.

## See Also

- [Core Concepts](02-core-concepts.md) - Understanding Program and Effect
- [Basic Effects](03-basic-effects.md) - State, Reader, Writer effects
- [Async Effects](04-async-effects.md) - Gather, Spawn, Time effects
- [Error Handling](05-error-handling.md) - Safe effect and RuntimeResult
- [API Reference](13-api-reference.md) - Complete API documentation