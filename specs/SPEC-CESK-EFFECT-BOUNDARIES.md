# SPEC: CESK Effect Boundaries

## Overview

doeff is an Effect monad processor with a stack of handlers. This spec clarifies the boundary between:

1. **Effects** - Handled inside doeff by the handler stack
2. **Escaped Effects** - Leave doeff's control, handled by external runtime

These are fundamentally different concepts that should not be conflated.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         doeff boundary                           │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Handler Stack (Effect Monad)                              │  │
│  │                                                            │  │
│  │    queue_handler                                           │  │
│  │      └─► scheduler_handler                                 │  │
│  │            └─► async_effects_handler                       │  │
│  │                  └─► core_handler                          │  │
│  │                        └─► User Program                    │  │
│  │                                                            │  │
│  │  Effects bubble UP through handlers until caught.          │  │
│  │  Handled effects stay INSIDE doeff.                        │  │
│  └───────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                              │ Unhandled effect                  │
│                              ▼                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  step() : CESKState → StepResult                           │  │
│  │                                                            │  │
│  │  StepResult = Done | Failed | CESKState | EscapedEffect    │  │
│  │                                            ↑               │  │
│  │                                   leaves doeff             │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
                               │
                               │ EscapedEffect (currently: Suspended)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                       External Runtime                           │
│                                                                  │
│  User's execution context: asyncio, ray, dask, threads, etc.    │
│                                                                  │
│  Runtime interprets the escaped effect however it wants.         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Concepts

### 1. Effects (Handled Inside doeff)

Effects are yielded by programs and caught by handlers in the stack.

```python
@do
def my_program():
    x = yield Get("key")      # caught by core_handler
    yield Put("key", x + 1)   # caught by core_handler
    t = yield Spawn(task())   # caught by scheduler_handler
    yield Wait(t)             # caught by scheduler_handler
    return x
```

All these effects are **handled inside doeff**. They never leave the system.

### 2. Escaped Effects (Leave doeff)

Some effects cannot be handled by any handler in the stack. They must escape to the caller.

```python
@do
def my_program():
    result = yield Await(some_coroutine())  # escapes to runtime
    return result
```

`Await` produces an effect that no handler can fully process. It must be:
1. Awaited in an asyncio event loop, OR
2. Run in a thread with its own event loop

This decision is **outside doeff's control**. The effect escapes.

### 3. EscapedEffect (Currently Named: Suspended)

When an effect escapes, `step()` returns an `EscapedEffect`:

```python
@dataclass(frozen=True)
class EscapedEffect:  # currently named: Suspended
    payload: Any                                    # what to run externally
    resume: Callable[[Any, Store], CESKState]       # continuation
    resume_error: Callable[[BaseException], CESKState]
    store: Store | None = None
```

This is the **Free monad** for CESK:

```
step : CESKState → Free[ExternalOp, StepResult]

where:
  Free.Pure(result)     = Done | Failed | CESKState
  Free.Bind(op, cont)   = EscapedEffect(payload=op, resume=cont)
```

The runtime is the **interpreter** for this Free monad.

---

## SuspendOn vs EscapedEffect (Suspended)

These are **completely different** concepts that should not be conflated.

### SuspendOn: Handler-Managed Async

Purpose: Let handlers manage their own executors (ray, dask, threads).

```
┌─────────────────────────────────────────────────────────────────┐
│  SuspendOn Flow                                                  │
│                                                                  │
│  1. Handler receives effect (e.g., RayRemoteEffect)              │
│  2. Handler submits work to its executor (ray.remote())          │
│  3. Handler creates a Future                                     │
│  4. Handler returns Future to user                               │
│  5. User does: yield Wait(future)                                │
│  6. Wait effect is handled by scheduler_handler                  │
│  7. Future resolves → program continues                          │
│                                                                  │
│  Everything stays INSIDE doeff.                                  │
│  Handler owns the executor. Future/Wait are normal effects.      │
└─────────────────────────────────────────────────────────────────┘
```

### EscapedEffect (Suspended): Effect Leaves doeff

Purpose: Signal that an effect cannot be handled internally.

```
┌─────────────────────────────────────────────────────────────────┐
│  EscapedEffect Flow                                              │
│                                                                  │
│  1. User does: yield Await(coroutine)                            │
│  2. Effect bubbles through handler stack                         │
│  3. No handler can fully handle it                               │
│  4. step() returns EscapedEffect(payload=coroutine, resume=...)  │
│  5. Effect LEAVES doeff                                          │
│  6. Runtime awaits coroutine in its event loop                   │
│  7. Runtime calls resume(value) to continue                      │
│                                                                  │
│  Effect escapes doeff. Runtime owns execution.                   │
└─────────────────────────────────────────────────────────────────┘
```

### Comparison

| Aspect | SuspendOn | EscapedEffect (Suspended) |
|--------|-----------|---------------------------|
| Purpose | Handler-managed executor | Effect escapes doeff |
| Who owns execution | Handler | External runtime |
| Stays inside doeff? | Yes | No |
| Uses Future/Wait? | Yes | No |
| Is Free monad? | No | Yes |

---

## Current Problem: _make_suspended_from_suspend_on

The function `_make_suspended_from_suspend_on` in `step.py` conflates these concepts:

```python
# step.py (PROBLEMATIC)
if isinstance(result, SuspendOn):
    return _make_suspended_from_suspend_on(result)  # converts to Suspended
```

This is wrong because:

1. **SuspendOn** means "handler is managing async internally"
2. **Suspended** means "effect escapes doeff"
3. Converting one to the other conflates their semantics

### The Fix

These should be separate paths:

```python
# Handler-managed async (SuspendOn)
# Handler creates Future, user Waits, stays inside doeff
# step() should NOT convert this to Suspended

# Escaped effect
# Effect cannot be handled, escapes to runtime
# step() returns EscapedEffect directly (not converted from SuspendOn)
```

---

## Proposed Type Changes

### Current (Confusing)

```python
class SuspendOn:       # overloaded meaning
    awaitable: Any
    stored_k: ...
    stored_store: ...

class Suspended:       # unclear purpose
    awaitable: Any
    awaitables: dict
    resume: Callable
    resume_error: Callable
```

### Proposed (Clear)

```python
# For handler-managed async (stays inside doeff)
class HandlerYield:
    """Handler yields control temporarily, will resume via Future/Wait."""
    future: Future
    continuation: Continuation

# For escaped effects (leaves doeff)  
class EscapedEffect:
    """Effect escapes doeff, runtime must handle."""
    payload: Any                    # what to run externally
    resume: Callable[[Any, Store], CESKState]
    resume_error: Callable[[BaseException], CESKState]
```

---

## Runtime as Free Monad Interpreter

The runtime interprets `EscapedEffect` (Free monad):

```python
class AsyncRuntime:
    """Interpreter: EscapedEffect → asyncio"""
    
    async def interpret(self, escaped: EscapedEffect) -> Any:
        if isawaitable(escaped.payload):
            return await escaped.payload
        elif callable(escaped.payload):
            return await loop.run_in_executor(None, escaped.payload)
        elif isinstance(escaped.payload, dict):
            # multi-task: await first completed
            ...

class SyncRuntime:
    """Interpreter: EscapedEffect → threads"""
    
    def interpret(self, escaped: EscapedEffect) -> Any:
        if callable(escaped.payload):
            return escaped.payload()
        elif isawaitable(escaped.payload):
            return run_in_thread_with_loop(escaped.payload)
```

Different runtimes = different interpreters for the same Free monad.

---

## Summary

| Concept | Inside/Outside doeff | Purpose |
|---------|---------------------|---------|
| Effect | Inside | Normal program operation |
| Handler | Inside | Catches and processes effects |
| SuspendOn | Inside | Handler-managed async (Future/Wait) |
| EscapedEffect | Outside | Effect leaves doeff for runtime |
| Runtime | Outside | Interpreter for escaped effects |

The key insight: **doeff is pure**. When doeff cannot handle something, it yields control via `EscapedEffect`. The runtime (outside doeff) decides how to execute it.

`_make_suspended_from_suspend_on` violates this by conflating internal async (SuspendOn) with escaped effects (Suspended). They should be cleanly separated.

---

## Future Direction: Parameterized CESK[M]

To make the effect boundary explicit in types, CESK should be parameterized by a monad `M`:

```
CESK[M] where M : Monad

step : CESKState → M[StepResult]
run  : Program[T] → M[RunResult[T]]
```

### Monad Options

```python
# M = Pure (Identity monad)
# No escaped effects allowed. Pure computation only.
step : CESKState → StepResult  # M[A] ≈ A
run  : Program[T] → RunResult[T]

# M = Awaitable (Async monad)  
# Escaped effects are awaitables for asyncio.
step : CESKState → Awaitable[StepResult]
run  : Program[T] → Awaitable[RunResult[T]]

# M = IO (Sync blocking monad)
# Escaped effects are thunks that may block.
step : CESKState → IO[StepResult]  # IO[A] ≈ () → A
run  : Program[T] → IO[RunResult[T]]
```

### Type Signature

```python
from typing import Generic, TypeVar

M = TypeVar("M")  # Monad type parameter
T = TypeVar("T")  # Result type

class CESKRunner(Generic[M]):
    """CESK runner parameterized by effect monad M."""
    
    def step(self, state: CESKState) -> M[StepResult]:
        ...
    
    def run(self, program: Program[T]) -> M[RunResult[T]]:
        ...

# Concrete instances
class PureRunner(CESKRunner[Identity]):
    """No escaped effects. Errors on Await/Delay."""
    pass

class AsyncRunner(CESKRunner[Awaitable]):
    """Escaped effects are awaitables."""
    pass

class SyncRunner(CESKRunner[IO]):
    """Escaped effects run in threads."""
    pass
```

### Result Type

```python
@dataclass
class RunResult(Generic[T]):
    """Result of running a program."""
    value: T
    state: dict[str, Any]
    log: list[Any]
    # ... other metadata

# With monad parameter explicit:
# run() returns M[RunResult[T]]
# 
# AsyncRunner.run(prog) → Awaitable[RunResult[T]]  (use: await runner.run(prog))
# SyncRunner.run(prog)  → IO[RunResult[T]]         (use: runner.run(prog)())
# PureRunner.run(prog)  → RunResult[T]             (use: runner.run(prog))
```

### Benefits

1. **Type-level clarity**: `M` makes execution context explicit
2. **No runtime surprises**: Can't accidentally use AsyncRunner in sync context
3. **Principled design**: CESK is pure, M captures external effects
4. **Extensibility**: New runners just instantiate with new M (trio, curio, ray, etc.)

### Migration Path

```
Current:
  AsyncRuntime.run(prog) → Awaitable[RuntimeResult[T]]
  SyncRuntime.run(prog)  → RuntimeResult[T]
  
  Problem: Suspended appears at runtime, confusing boundary
  
Future:
  CESKRunner[Awaitable].run(prog) → Awaitable[RunResult[T]]
  CESKRunner[IO].run(prog)        → IO[RunResult[T]]
  CESKRunner[Identity].run(prog)  → RunResult[T]
  
  Benefit: M is explicit, no Suspended confusion
```

This makes doeff's effect boundary a first-class citizen in the type system.
