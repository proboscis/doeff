# Effects, Handlers, and Runtime Support Matrix

This document provides a comprehensive overview of all effects defined in doeff, their handler implementations, runtime support, and test coverage status.

## Quick Reference

| Status | Meaning |
|--------|---------|
| Supported | Handler registered in `default_handlers()`, tested |
| Partial | Works in some runtimes only |
| Not Implemented | Effect defined but no CESK handler |

## Core Effects (Reader/State/Writer)

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------|
| `AskEffect` | `Ask(key)` | `core.py` | Supported | Supported | Yes |
| `LocalEffect` | `Local(env, program)` | `control.py` | Supported | Supported | Yes |
| `StateGetEffect` | `Get(key)` | `core.py` | Supported | Supported | Yes |
| `StatePutEffect` | `Put(key, value)` | `core.py` | Supported | Supported | Yes |
| `StateModifyEffect` | `Modify(key, func)` | `core.py` | Supported | Supported | Yes |
| `WriterTellEffect` | `Tell(msg)` / `Log(msg)` | `control.py` | Supported | Supported | Yes |
| `WriterListenEffect` | `Listen(program)` | `control.py` | Supported | Supported | Yes |

## Control Flow Effects

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------|
| `PureEffect` | `Pure(value)` | `core.py` | Supported | Supported | Yes |
| `ResultSafeEffect` | `Safe(program)` | `control.py` | Supported | Supported | Yes |
| `InterceptEffect` | `intercept_program_effect()` | `control.py` | Supported | Supported | Yes |

## IO Effects

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------|
| `IOPerformEffect` | `IO(action)` | `io.py` | Supported | Supported | Yes |

## Cache Effects

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------|
| `CacheGetEffect` | `CacheGet(key)` | `io.py` | Supported | Supported | Yes |
| `CachePutEffect` | `CachePut(key, value)` | `io.py` | Supported | Supported | Yes |
| `CacheExistsEffect` | `CacheExists(key)` | `io.py` | Supported | Supported | Yes |
| `CacheDeleteEffect` | `CacheDelete(key)` | `io.py` | Supported | Supported | Yes |

## Time Effects

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------|
| `DelayEffect` | `Delay(seconds)` | `time.py` | Supported (real sleep) | Supported (advances sim time) | Yes |
| `GetTimeEffect` | `GetTime()` | `time.py` | Supported | Supported | Yes |
| `WaitUntilEffect` | `WaitUntil(datetime)` | `time.py` | Supported (real wait) | Supported (advances sim time) | Yes |

## Concurrency Effects

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------|
| `GatherEffect` | `Gather(*programs)` | `task.py` | Supported (sequential) | Supported (sequential) | Yes |
| `SpawnEffect` | `Spawn(program)` | - | Not Implemented | Not Implemented | No |
| `TaskJoinEffect` | `task.join()` | - | Not Implemented | Not Implemented | No |
| `FutureAwaitEffect` | `Await(awaitable)` | - | Not Implemented | Not Implemented | No |

## Atomic Effects

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------|
| `AtomicGetEffect` | `AtomicGet(key)` | - | Not Implemented | Not Implemented | No |
| `AtomicUpdateEffect` | `AtomicUpdate(key, updater)` | - | Not Implemented | Not Implemented | No |

## Debug/Introspection Effects

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------|
| `ProgramCallFrameEffect` | `ProgramCallFrame(depth)` | - | Not Implemented | Not Implemented | No |
| `ProgramCallStackEffect` | `ProgramCallStack()` | - | Not Implemented | Not Implemented | No |

## Graph Effects

| Effect | Constructor | Handler | SyncRuntime | SimulationRuntime | Tested |
|--------|-------------|---------|-------------|-------------------|--------|
| `GraphStepEffect` | `Step(value, meta)` | - | Not Implemented | Not Implemented | No |
| `GraphAnnotateEffect` | `Annotate(meta)` | - | Not Implemented | Not Implemented | No |
| `GraphSnapshotEffect` | `Snapshot()` | - | Not Implemented | Not Implemented | No |
| `GraphCaptureEffect` | `CaptureGraph(program)` | - | Not Implemented | Not Implemented | No |

## Runtime Comparison

| Runtime | Location | Status | Time Handling |
|---------|----------|--------|---------------|
| `SyncRuntime` | `cesk/runtime/sync.py` | Active | Real wall-clock (uses `time.sleep`) |
| `SimulationRuntime` | `cesk/runtime/simulation.py` | Active | Simulated time (instant advancement) |
| `AsyncioRuntime` | - | Not Ported | Was in old `runtimes/`, not available in CESK |

## Handler Registration

All supported handlers are registered via `default_handlers()` in `doeff/cesk/handlers/__init__.py`:

```python
from doeff.cesk.handlers import default_handlers

handlers = default_handlers()
# Returns dict mapping effect types to handler functions
```

Custom handlers can be passed to runtimes:

```python
from doeff.cesk.runtime import SyncRuntime

custom_handlers = default_handlers()
custom_handlers[MyEffect] = my_handler

runtime = SyncRuntime(handlers=custom_handlers)
```

## Future Work

The following effects are defined but not yet implemented in the CESK runtime:

### Concurrency (Spawn/Task/Await)
These effects require async runtime support or multi-threading capabilities not yet implemented in the CESK machine.

### Atomic Operations
These effects are designed for thread-safe state updates in concurrent scenarios.

### Call Stack Introspection
These effects would allow programs to inspect their own call stack for debugging or metaprogramming.

### Graph Tracking
These effects are for computation graph construction and visualization. May require specialized runtime support.

## See Also

- [Core Concepts](02-core-concepts.md) - Understanding Program and Effect
- [Basic Effects](03-basic-effects.md) - State, Reader, Writer effects
- [API Reference](13-api-reference.md) - Complete API documentation
