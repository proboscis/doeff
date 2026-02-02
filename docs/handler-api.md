# CESK Handler API Reference

This document provides comprehensive documentation for implementing custom effect handlers in the doeff framework. The CESK machine (Control, Environment, Store, Kontinuation) is the interpreter core that executes effect programs.

## Table of Contents

- [Overview](#overview)
- [Handler Signature](#handler-signature)
- [HandlerContext](#handlercontext)
- [Return Values](#return-values)
- [Registering Custom Handlers](#registering-custom-handlers)
- [Examples](#examples)
  - [Simple Sync Handler](#simple-sync-handler)
  - [State-Modifying Handler](#state-modifying-handler)
  - [Forwarding Unhandled Effects](#forwarding-unhandled-effects)
  - [Error-Producing Handler](#error-producing-handler)
- [Migration Guide](#migration-guide)
- [Best Practices](#best-practices)

---

## Overview

Effect handlers in doeff are `@do` functions that process effects and return `CESKState` or plain values. Handlers are stacked from outermost to innermost, with effects bubbling up through the handler stack.

```python
from doeff import do
from doeff._types_internal import EffectBase
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.state import CESKState

@do
def my_handler(effect: EffectBase, ctx: HandlerContext):
    if isinstance(effect, MyEffect):
        # Handle the effect - return CESKState
        result = do_something(effect.data)
        return CESKState.with_value(result, ctx.env, ctx.store, ctx.k)

    # Forward unhandled effects to outer handlers
    result = yield effect
    return result
```

---

## Handler Signature

Every effect handler follows this signature:

```python
from doeff import do
from doeff._types_internal import EffectBase
from doeff.cesk.handler_frame import HandlerContext

@do
def my_handler(
    effect: EffectBase,
    ctx: HandlerContext,
):
    # Handle or forward the effect
    ...
```

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `effect` | `EffectBase` | The effect instance being handled |
| `ctx` | `HandlerContext` | Context with store, env, and continuations |

### Return Value

Handlers return one of:

- `CESKState` - Direct state with value, error, or program
- Plain value - Automatically wrapped by the handler result frame
- `ResumeK` - Switch to a different continuation (advanced)

---

## HandlerContext

`HandlerContext` provides access to execution state:

```python
from doeff.cesk.handler_frame import HandlerContext

@dataclass
class HandlerContext:
    store: Store           # Current store (mutable state dict)
    env: Environment       # Current environment (FrozenDict)
    delimited_k: Kontinuation  # Continuation up to this handler
    handler_depth: int     # Depth in handler stack (0 = outermost)
    outer_k: Kontinuation  # Continuation beyond this handler

    @property
    def k(self) -> Kontinuation:
        """Full continuation: delimited_k + outer_k."""
        return list(self.delimited_k) + list(self.outer_k)
```

### Key Properties

| Property | Type | Description |
|----------|------|-------------|
| `store` | `dict[str, Any]` | Shared mutable state across all tasks |
| `env` | `FrozenDict` | Immutable environment (reader context) |
| `k` | `list[Frame]` | Full continuation stack |
| `delimited_k` | `list[Frame]` | Continuation up to this handler only |
| `handler_depth` | `int` | Nesting depth (0 = outermost handler) |

### Reserved Store Keys

| Key | Description |
|-----|-------------|
| `__log__` | Accumulated `Tell`/`Log` messages (list) |
| `__cache_storage__` | Cache effect storage |
| `__current_time__` | Simulated current time |
| `__graph__` | Graph tracking data |
| `__ask_lazy_cache__` | Lazy `Ask` evaluation cache |

---

## Return Values

### CESKState

The primary way to return from a handler:

```python
from doeff.cesk.state import CESKState

# Return a value
return CESKState.with_value(result, ctx.env, ctx.store, ctx.k)

# Return an error
return CESKState.with_error(exception, ctx.env, ctx.store, ctx.k)

# Return a program to evaluate
return CESKState.with_program(sub_program, ctx.env, ctx.store, ctx.k)
```

### Plain Values

When you return a plain value (not `CESKState`), the handler result frame automatically wraps it:

```python
@do
def my_handler(effect, ctx):
    if isinstance(effect, MyEffect):
        return CESKState.with_value(42, ctx.env, ctx.store, ctx.k)

    # Forward and return plain value
    result = yield effect
    return result  # Plain value - automatically handled
```

### ResumeK (Advanced)

For advanced control flow, use `ResumeK` to switch continuations:

```python
from doeff.cesk.handler_frame import ResumeK

return ResumeK(
    k=different_continuation,
    value=some_value,
    env=ctx.env,
    store=ctx.store,
)
```

---

## Registering Custom Handlers

Custom handlers are added to the handler list when calling `sync_run` or `async_run`. Handlers are stacked from outermost to innermost:

```python
from doeff import sync_run, sync_handlers_preset, do
from doeff._types_internal import EffectBase
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.state import CESKState

@do
def my_custom_handler(effect: EffectBase, ctx: HandlerContext):
    if isinstance(effect, MyCustomEffect):
        result = handle_my_effect(effect)
        return CESKState.with_value(result, ctx.env, ctx.store, ctx.k)

    # Forward unhandled effects
    result = yield effect
    return result

# Prepend custom handler to preset (outermost handlers first)
handlers = [my_custom_handler, *sync_handlers_preset]
result = sync_run(my_program(), handlers)
```

### Handler Preset Structure

The default presets include these handlers (outermost to innermost):

```python
sync_handlers_preset = [
    scheduler_state_handler,   # Task queue management
    task_scheduler_handler,    # Spawn/Wait/Gather/Race
    sync_await_handler,        # Await via background thread
    core_handler,              # Get/Put/Ask/Log/etc.
]

async_handlers_preset = [
    scheduler_state_handler,
    task_scheduler_handler,
    python_async_syntax_escape_handler,  # Async escape for await
    core_handler,
]
```

Custom handlers should typically be prepended to handle specific effects before the defaults.

---

## Examples

### Simple Sync Handler

A handler that returns an immediate value without modifying state:

```python
from dataclasses import dataclass
from doeff import do
from doeff._types_internal import EffectBase
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.state import CESKState


@dataclass(frozen=True)
class GetEnvVar(EffectBase):
    """Effect to get an environment variable."""
    name: str


@do
def env_var_handler(effect: EffectBase, ctx: HandlerContext):
    if isinstance(effect, GetEnvVar):
        import os
        value = os.environ.get(effect.name)
        return CESKState.with_value(value, ctx.env, ctx.store, ctx.k)

    # Forward unhandled effects
    result = yield effect
    return result
```

### State-Modifying Handler

A handler that updates the shared store:

```python
from dataclasses import dataclass
from doeff import do
from doeff._types_internal import EffectBase
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.state import CESKState


@dataclass(frozen=True)
class IncrementCounter(EffectBase):
    """Effect to increment a named counter."""
    name: str
    amount: int = 1


@do
def counter_handler(effect: EffectBase, ctx: HandlerContext):
    if isinstance(effect, IncrementCounter):
        counters = ctx.store.get("__counters__", {})
        current = counters.get(effect.name, 0)
        new_value = current + effect.amount

        # Create new store with updated counters
        new_counters = {**counters, effect.name: new_value}
        new_store = {**ctx.store, "__counters__": new_counters}

        return CESKState.with_value(new_value, ctx.env, new_store, ctx.k)

    # Forward unhandled effects
    result = yield effect
    return result
```

### Forwarding Unhandled Effects

A key pattern is forwarding effects your handler doesn't handle:

```python
@do
def my_handler(effect: EffectBase, ctx: HandlerContext):
    if isinstance(effect, MyEffect):
        # Handle this effect type
        return CESKState.with_value(result, ctx.env, ctx.store, ctx.k)

    if isinstance(effect, AnotherEffect):
        # Handle this effect type too
        return CESKState.with_value(other_result, ctx.env, ctx.store, ctx.k)

    # IMPORTANT: Forward all other effects to outer handlers
    result = yield effect
    return result
```

If you don't forward unhandled effects, they will cause `UnhandledEffectError`.

### Error-Producing Handler

A handler that may produce errors:

```python
from dataclasses import dataclass
from doeff import do
from doeff._types_internal import EffectBase
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.state import CESKState


@dataclass(frozen=True)
class DivideEffect(EffectBase):
    """Effect to perform division."""
    numerator: float
    denominator: float


@do
def divide_handler(effect: EffectBase, ctx: HandlerContext):
    if isinstance(effect, DivideEffect):
        if effect.denominator == 0:
            return CESKState.with_error(
                ZeroDivisionError("Cannot divide by zero"),
                ctx.env, ctx.store, ctx.k
            )

        result = effect.numerator / effect.denominator
        return CESKState.with_value(result, ctx.env, ctx.store, ctx.k)

    # Forward unhandled effects
    result = yield effect
    return result
```

---

## Migration Guide

### Handler Signature Changes (v1 → v2)

The handler system has been completely redesigned:

| v1 (deprecated) | v2 (current) |
|-----------------|--------------|
| `def handler(effect, task_state, store)` | `@do def handler(effect, ctx: HandlerContext)` |
| Return `ContinueValue`, `ContinueError`, etc. | Return `CESKState` or plain value |
| `default_handlers()` dict | Handler list with presets |

**v1 (deprecated):**
```python
from doeff.cesk.frames import ContinueValue

def my_handler(effect, task_state, store):
    return ContinueValue(
        value=result,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )
```

**v2 (current):**
```python
from doeff import do
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.state import CESKState

@do
def my_handler(effect, ctx: HandlerContext):
    if isinstance(effect, MyEffect):
        return CESKState.with_value(result, ctx.env, ctx.store, ctx.k)

    # Forward unhandled effects
    result = yield effect
    return result
```

### Return Type Changes

| v1 Type | v2 Equivalent |
|---------|---------------|
| `ContinueValue(value, env, store, k)` | `CESKState.with_value(value, env, store, k)` |
| `ContinueError(error, env, store, k)` | `CESKState.with_error(error, env, store, k)` |
| `ContinueProgram(prog, env, store, k)` | `CESKState.with_program(prog, env, store, k)` |

### Import Changes

| Old Import | New Import |
|------------|------------|
| `from doeff.cesk.handlers import default_handlers` | Use `sync_handlers_preset` or `async_handlers_preset` |
| `from doeff.cesk.state import TaskState` | `from doeff.cesk.handler_frame import HandlerContext` |
| `from doeff.cesk.frames import ContinueValue` | `from doeff.cesk.state import CESKState` |

### Complete Migration Example

**v1 (deprecated):**
```python
from dataclasses import dataclass
from doeff._types_internal import EffectBase
from doeff.cesk.frames import ContinueValue
from doeff.cesk.handlers import default_handlers
from doeff.cesk.runtime import SyncRuntime

@dataclass(frozen=True)
class MyEffect(EffectBase):
    value: int

def handle_my_effect(effect, task_state, store):
    result = effect.value * 2
    return ContinueValue(
        value=result,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )

handlers = default_handlers()
handlers[MyEffect] = handle_my_effect

runtime = SyncRuntime(handlers=handlers)
result = runtime.run(my_program())
```

**v2 (current):**
```python
from dataclasses import dataclass
from doeff import do, sync_run, sync_handlers_preset
from doeff._types_internal import EffectBase
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.state import CESKState

@dataclass(frozen=True)
class MyEffect(EffectBase):
    value: int

@do
def my_effect_handler(effect, ctx: HandlerContext):
    if isinstance(effect, MyEffect):
        result = effect.value * 2
        return CESKState.with_value(result, ctx.env, ctx.store, ctx.k)

    # Forward unhandled effects
    result = yield effect
    return result

# Prepend custom handler to preset
handlers = [my_effect_handler, *sync_handlers_preset]
result = sync_run(my_program(), handlers)
```

### Migration Checklist

- [ ] Add `@do` decorator to handler functions
- [ ] Change signature from `(effect, task_state, store)` to `(effect, ctx: HandlerContext)`
- [ ] Replace `ContinueValue(...)` with `CESKState.with_value(...)`
- [ ] Replace `ContinueError(...)` with `CESKState.with_error(...)`
- [ ] Access `env` via `ctx.env`, `store` via `ctx.store`, `k` via `ctx.k`
- [ ] Add effect forwarding: `result = yield effect; return result`
- [ ] Replace `default_handlers()` with `sync_handlers_preset` or `async_handlers_preset`
- [ ] Replace `SyncRuntime(handlers=h).run(p)` with `sync_run(p, handlers)`
- [ ] Replace `AsyncRuntime(handlers=h).run(p)` with `await async_run(p, handlers)`

---

## Best Practices

### 1. Always Forward Unhandled Effects

Every handler must forward effects it doesn't handle, or they'll cause `UnhandledEffectError`:

```python
@do
def my_handler(effect, ctx):
    if isinstance(effect, MyEffect):
        # Handle this specific effect
        return CESKState.with_value(result, ctx.env, ctx.store, ctx.k)

    # CRITICAL: Forward all other effects
    result = yield effect
    return result
```

### 2. Keep Handlers Pure When Possible

Handlers should ideally be deterministic functions of their inputs. Side effects should be modeled as effects themselves:

```python
# Good: Return computed value
@do
def handle_add(effect, ctx):
    if isinstance(effect, AddEffect):
        return CESKState.with_value(
            effect.a + effect.b,
            ctx.env, ctx.store, ctx.k
        )
    result = yield effect
    return result

# Avoid: Side effects in handler
@do
def handle_add_bad(effect, ctx):
    if isinstance(effect, AddEffect):
        print(f"Adding {effect.a} + {effect.b}")  # Side effect!
        ...
```

### 3. Don't Mutate Store In-Place

Create a new store dictionary when modifying:

```python
# Good: Create new store
new_store = {**ctx.store, "key": new_value}
return CESKState.with_value(result, ctx.env, new_store, ctx.k)

# Avoid: Mutate in place (can cause subtle bugs)
ctx.store["key"] = new_value  # Don't do this!
```

### 4. Preserve Environment Unless Intentionally Changing Scope

Most handlers should pass through `ctx.env` unchanged:

```python
return CESKState.with_value(result, ctx.env, ctx.store, ctx.k)
```

Only modify `env` for scoping effects like `Local`.

### 5. Type Your Handlers

Use proper type annotations for better IDE support:

```python
from doeff import do
from doeff._types_internal import EffectBase
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.state import CESKState

@do
def my_handler(effect: EffectBase, ctx: HandlerContext):
    ...
```

### 6. Handler Order Matters

Handlers are stacked outermost to innermost. Effects bubble from inner to outer until handled:

```python
# Effect flow: core_handler → ... → scheduler_handler → your_handler
handlers = [your_handler, *sync_handlers_preset]
```

Put your custom handler first if you want it to intercept effects before the defaults.

---

## See Also

- [Runtime and Execution Model](20-runtime-scheduler.md) - sync_run, async_run, handler presets
- [Effects Matrix](21-effects-matrix.md) - Complete effect support reference
- [Error Handling](05-error-handling.md) - Safe effect and error propagation
- [Core Concepts](02-core-concepts.md) - Programs and Effects overview
