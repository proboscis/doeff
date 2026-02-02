# Effects, Handlers, and Runtime Support Matrix

This document provides a comprehensive overview of all effects defined in doeff, their handler implementations, runtime support, and test coverage status.

## Quick Reference

| Status | Meaning |
|--------|---------|
| Supported | Handler in `sync_handlers_preset`/`async_handlers_preset`, tested |
| Intercepted | Runner intercepts effect directly (async handling) |
| Sequential | Runs sequentially in sync_run |

## Core Effects (Reader/State/Writer)

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | AsyncRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------------|--------|
| `AskEffect` | `Ask(key)` | `core.py` | Supported | Supported | Supported | Yes |
| `LocalEffect` | `Local(env, program)` | `control.py` | Supported | Supported | Supported | Yes |
| `StateGetEffect` | `Get(key)` | `core.py` | Supported | Supported | Supported | Yes |
| `StatePutEffect` | `Put(key, value)` | `core.py` | Supported | Supported | Supported | Yes |
| `StateModifyEffect` | `Modify(key, func)` | `core.py` | Supported | Supported | Supported | Yes |
| `WriterTellEffect` | `Tell(msg)` / `Log(msg)` | `control.py` | Supported | Supported | Supported | Yes |
| `WriterListenEffect` | `Listen(program)` | `control.py` | Supported | Supported | Supported | Yes |

### Ask Lazy Program Evaluation

When an environment value is a `Program`, it is evaluated lazily on first `Ask` access. The result is cached for subsequent accesses within the same execution:

```python
@do
def use_config():
    # If env["config"] is a Program, it runs lazily here
    config = yield Ask("config")
    return config

# Pass a Program as env value - evaluated lazily on first Ask
result = sync_run(
    use_config(),
    sync_handlers_preset,
    env={"config": expensive_computation()}  # Program, not value
)
```

See [SPEC-EFF-001](../specs/effects/SPEC-EFF-001-reader.md) for details.

## Control Flow Effects

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | AsyncRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------------|--------|
| `PureEffect` | `Pure(value)` | `core.py` | Supported | Supported | Supported | Yes |
| `ResultSafeEffect` | `Safe(program)` | `control.py` | Supported | Supported | Supported | Yes |
| `InterceptEffect` | `intercept_program_effect()` | `control.py` | Supported | Supported | Supported | Yes |

## IO Effects

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | AsyncRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------------|--------|
| `IOPerformEffect` | `IO(action)` | `io.py` | Supported | Supported | Supported | Yes |

## Cache Effects

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | AsyncRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------------|--------|
| `CacheGetEffect` | `CacheGet(key)` | `io.py` | Supported | Supported | Supported | Yes |
| `CachePutEffect` | `CachePut(key, value)` | `io.py` | Supported | Supported | Supported | Yes |
| `CacheExistsEffect` | `CacheExists(key)` | `io.py` | Supported | Supported | Supported | Yes |
| `CacheDeleteEffect` | `CacheDelete(key)` | `io.py` | Supported | Supported | Supported | Yes |

## Time Effects

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | AsyncRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------------|--------|
| `DelayEffect` | `Delay(seconds)` | `time.py` | Supported (real sleep) | Supported (sim time) | Intercepted (async sleep) | Yes |
| `GetTimeEffect` | `GetTime()` | `time.py` | Supported | Supported | Supported | Yes |
| `WaitUntilEffect` | `WaitUntil(datetime)` | `time.py` | Supported (real wait) | Supported (sim time) | Intercepted (async wait) | Yes |

### Time Effect Handling Note

- **SyncRuntime**: Uses real wall-clock time (`time.sleep`)
- **SimulationRuntime**: Intercepts time effects and advances simulated time instantly
- **AsyncRuntime**: Intercepts time effects for `asyncio.sleep` integration

## Concurrency Effects

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | AsyncRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------------|--------|
| `SpawnEffect` | `Spawn(program)` | Runtime | Cooperative | Cooperative | Intercepted (asyncio) | Yes |
| `WaitEffect` | `Wait(future)` | Runtime | Cooperative | Cooperative | Intercepted | Yes |
| `GatherEffect` | `Gather(*futures)` | Runtime | Cooperative | Cooperative | Intercepted (parallel) | Yes |
| `RaceEffect` | `Race(*futures)` | Runtime | Cooperative | Cooperative | Intercepted | Yes |
| `TaskCancelEffect` | `task.cancel()` | Runtime | Cooperative | Cooperative | Intercepted | Yes |
| `FutureAwaitEffect` | `Await(awaitable)` | Runtime | Not Supported | Not Supported | Intercepted | Yes |

### Cooperative Scheduling (SyncRuntime / SimulationRuntime)

Both `SyncRuntime` and `SimulationRuntime` implement concurrency via **cooperative scheduling**:

- **Single-threaded**: All task code runs on one thread (no races in user code)
- **Interleaved execution**: Tasks yield control at every `yield` statement
- **Deterministic**: Execution order is reproducible for pure compute
- **Timer threads**: `Delay` uses background threads for timing (SyncRuntime only)

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

| Aspect | AsyncRuntime | SyncRuntime / SimulationRuntime |
|--------|--------------|----------------------------------|
| `Spawn` | asyncio.create_task | Enqueue to task scheduler |
| `Gather` | asyncio.gather (true parallel) | Cooperative interleaving |
| Parallelism | Yes (kernel I/O multiplexing) | No (single-threaded) |
| Deterministic | No | Yes |
| `Await` support | Yes | No |

## Atomic Effects

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | AsyncRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------------|--------|
| `AtomicGetEffect` | `AtomicGet(key)` | `atomic.py` | Supported | Supported | Supported | Yes |
| `AtomicUpdateEffect` | `AtomicUpdate(key, updater)` | `atomic.py` | Supported | Supported | Supported | Yes |

## Debug/Introspection Effects

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | AsyncRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------------|--------|
| `ProgramCallFrameEffect` | `ProgramCallFrame(depth)` | `callstack.py` | Supported | Supported | Supported | Yes |
| `ProgramCallStackEffect` | `ProgramCallStack()` | `callstack.py` | Supported | Supported | Supported | Yes |

## Graph Effects

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | AsyncRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------------|--------|
| `GraphStepEffect` | `Step(value, meta)` | `graph.py` | Supported | Supported | Supported | Yes |
| `GraphAnnotateEffect` | `Annotate(meta)` | `graph.py` | Supported | Supported | Supported | Yes |
| `GraphSnapshotEffect` | `Snapshot()` | `graph.py` | Supported | Supported | Supported | Yes |
| `GraphCaptureEffect` | `CaptureGraph(program)` | `graph.py` | Supported | Supported | Supported | Yes |

## Execution Function Comparison

| Function | Time Handling | Concurrency |
|----------|---------------|-------------|
| `async_run` | Real async (`asyncio.sleep`) | Parallel (asyncio) |
| `sync_run` | Real blocking + timer threads | Cooperative scheduler |

### Selection Guide

| Use Case | Recommended |
|----------|-------------|
| Production async code | `async_run` |
| True parallel I/O | `async_run` |
| Python coroutine interop (`Await`) | `async_run` |
| Scripts, CLI tools | `sync_run` |
| Concurrent tasks without asyncio | `sync_run` |
| Testing with controlled time | `sync_run` with `__current_time__` in store |
| Deterministic concurrent testing | `sync_run` |

## Handler Registration

Default handlers are available via preset lists:

```python
from doeff import sync_handlers_preset, async_handlers_preset

# Use presets directly with sync_run/async_run
result = sync_run(my_program(), sync_handlers_preset)
```

Custom handlers can be prepended to presets:

```python
from doeff import do, sync_run, sync_handlers_preset
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
handlers = [my_handler, *sync_handlers_preset]
result = sync_run(my_program(), handlers)
```

## RuntimeResult

Both `sync_run()` and `async_run()` return `RuntimeResult[T]`:

```python
from doeff import sync_run, sync_handlers_preset

result = sync_run(my_program(), sync_handlers_preset)

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
