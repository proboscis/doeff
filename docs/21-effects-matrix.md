# Effects, Handlers, and Runtime Support Matrix

This document provides a comprehensive overview of all effects defined in doeff, their handler implementations, runtime support, and test coverage status.

## Quick Reference

| Status | Meaning |
|--------|---------|
| Supported | Handler registered in `default_handlers()`, tested |
| Intercepted | Runtime intercepts effect directly (async handling) |
| Sequential | Runs sequentially in sync runtimes |

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

When you pass a `Program` as the default value to `Ask`, it is evaluated lazily only if the key is missing. The result is cached for subsequent accesses within the same execution:

```python
@do
def with_lazy_default():
    # expensive_computation() only runs if "config" is missing
    config = yield Ask("config", default=expensive_computation())
    return config
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
| `GatherEffect` | `Gather(*programs)` | `task.py` | Sequential | Sequential | Intercepted (parallel) | Yes |
| `SpawnEffect` | `Spawn(program)` | Runtime | Not Supported | Not Supported | Intercepted | Yes |
| `TaskJoinEffect` | `task.join()` | Runtime | Not Supported | Not Supported | Intercepted | Yes |
| `TaskCancelEffect` | `task.cancel()` | Runtime | Not Supported | Not Supported | Intercepted | Yes |
| `TaskIsDoneEffect` | `task.is_done()` | Runtime | Not Supported | Not Supported | Intercepted | Yes |
| `FutureAwaitEffect` | `Await(awaitable)` | Runtime | Not Supported | Not Supported | Intercepted | Yes |

### Spawn/Task Background Execution

The `Spawn` effect creates background tasks with **snapshot semantics**:
- Environment and store are snapshotted at spawn time
- Spawned tasks run with isolated state (no mutation of parent's store)
- Use `task.join()` to wait for completion and retrieve results
- Use `task.cancel()` to request cancellation
- Use `task.is_done()` to check completion status

```python
@do
def background_work():
    task = yield Spawn(expensive_computation())
    # ... do other work ...
    result = yield task.join()  # Wait for completion
    return result
```

See [SPEC-EFF-005](../specs/effects/SPEC-EFF-005-concurrency.md) for details.

### Gather Parallel Execution

`Gather` runs multiple programs and collects their results:
- **AsyncRuntime**: True parallel execution via asyncio
- **SyncRuntime/SimulationRuntime**: Sequential execution (same semantics, different performance)

```python
@do
def parallel_fetch():
    results = yield Gather(fetch_a(), fetch_b(), fetch_c())
    return results  # [result_a, result_b, result_c]
```

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

## Runtime Comparison

| Runtime | Location | Status | Time Handling | Concurrency |
|---------|----------|--------|---------------|-------------|
| `AsyncRuntime` | `cesk/runtime/async_.py` | **Primary** | Real async (`asyncio.sleep`) | Parallel Gather, Spawn/Task |
| `SyncRuntime` | `cesk/runtime/sync.py` | Active | Real blocking (`time.sleep`) | Sequential Gather |
| `SimulationRuntime` | `cesk/runtime/simulation.py` | Active | Simulated (instant) | Sequential Gather |

### Runtime Selection Guide

| Use Case | Recommended Runtime |
|----------|---------------------|
| Production async code | `AsyncRuntime` |
| Scripts, CLI tools | `SyncRuntime` |
| Testing with controlled time | `SimulationRuntime` |
| Background task execution | `AsyncRuntime` (required for Spawn) |

## Handler Registration

All supported handlers are registered via `default_handlers()` in `doeff/cesk/handlers/__init__.py`:

```python
from doeff.cesk.handlers import default_handlers

handlers = default_handlers()
# Returns dict mapping effect types to handler functions
```

Custom handlers can be passed to runtimes:

```python
from doeff.cesk.runtime import AsyncRuntime

custom_handlers = default_handlers()
custom_handlers[MyEffect] = my_handler

runtime = AsyncRuntime(handlers=custom_handlers)
```

## RuntimeResult

All runtimes return `RuntimeResult[T]` from their `run()` method:

```python
from doeff.cesk.runtime import AsyncRuntime

runtime = AsyncRuntime()
result = await runtime.run(my_program())

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
