# SPEC-CESK-003: Minimal Frame Architecture

## Status: Draft

## Summary

This spec proposes reducing the Frame system to just two types: `ReturnFrame` and `HandlerFrame`. All other Frame types (SafeFrame, ListenFrame, LocalFrame, InterceptFrame, GatherFrame, RaceFrame) can be implemented in user-space using `WithHandler` + `@do` + Python try-except, or as scheduler effects.

## Key Insight

The current `WithHandler` mechanism already provides complete algebraic effects capabilities:

```python
result = yield WithHandler(handler=my_handler, expr=computation)
#        ↑                         ↑                    ↑
#   final value              effect clause         scoped block
#   (return clause)          (intercept OUT)       (computation)

try:
    result = yield WithHandler(...)
except Exception as e:
    result = default  # exception clause
```

This means:
- **Return clause**: `yield WithHandler(...)` returns the final value
- **Effect clause**: `handler` intercepts effects going out
- **Exception clause**: Python `try-except` catches errors

No additional `on_return`/`on_error` callbacks are needed.

## Current Frame Types

```
┌─────────────────────────────────────────────────────────────────┐
│                    CURRENT: 8+ FRAME TYPES                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  FUNDAMENTAL (must keep):                                       │
│    ReturnFrame      - @do generator boundary                    │
│    HandlerFrame     - WithHandler effect interception           │
│                                                                 │
│  CAN BE USER-SPACE (via WithHandler):                           │
│    SafeFrame        - error recovery                            │
│    ListenFrame      - log capture                               │
│    LocalFrame       - scoped environment                        │
│    InterceptFrame   - effect transformation                     │
│                                                                 │
│  CAN BE SCHEDULER EFFECTS:                                      │
│    GatherFrame      - concurrent task coordination              │
│    RaceFrame        - concurrent task racing                    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Target Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    TARGET: 2 FRAME TYPES                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ReturnFrame   - marks @do generator boundaries                 │
│  HandlerFrame  - implements WithHandler effect interception     │
│                                                                 │
│  Everything else is:                                            │
│    - User-space helper functions using WithHandler              │
│    - Scheduler effects for concurrency coordination             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## User-Space Implementations

### Safe → `with_safe()`

```python
# CURRENT: Built-in SafeFrame
@do
def program():
    result = yield Safe(risky_computation(), default="fallback")
    return result

# TARGET: User-space implementation
@do
def with_safe(program: Program[T], default: T) -> Program[T]:
    """Catch errors and return default value."""
    try:
        result = yield WithHandler(
            handler=forward_all_effects,
            expr=program,
        )
        return result
    except Exception:
        return default

# Usage unchanged for users (Safe effect calls with_safe internally)
```

### Listen → `with_listen()`

```python
# CURRENT: Built-in ListenFrame
@do
def program():
    result = yield Listen(computation_with_tells())
    return result.value, result.log

# TARGET: User-space implementation
@do
def with_listen(program: Program[T]) -> Program[tuple[T, list]]:
    """Capture Tell effects into a log."""
    log: list[Any] = []

    def tell_handler(effect: EffectBase, ctx: HandlerContext) -> Program[CESKState]:
        if isinstance(effect, Tell):
            log.append(effect.message)
            return Program.pure(
                CESKState.with_value(None, ctx.env, ctx.store, ctx.delimited_k)
            )
        # Forward other effects
        @do
        def forward():
            result = yield effect
            return CESKState.with_value(result, ctx.env, ctx.store, ctx.delimited_k)
        return forward()

    result = yield WithHandler(handler=tell_handler, expr=program)
    return (result, log)
```

### Local → `with_local()`

```python
# CURRENT: Built-in LocalFrame
@do
def program():
    result = yield Local({"timeout": 30}, inner_computation())
    return result

# TARGET: User-space implementation
@do
def with_local(env_updates: dict, program: Program[T]) -> Program[T]:
    """Run program with modified environment."""
    def ask_handler(effect: EffectBase, ctx: HandlerContext) -> Program[CESKState]:
        if isinstance(effect, Ask) and effect.key in env_updates:
            return Program.pure(
                CESKState.with_value(env_updates[effect.key], ctx.env, ctx.store, ctx.delimited_k)
            )
        # Forward to outer handler
        @do
        def forward():
            result = yield effect
            return CESKState.with_value(result, ctx.env, ctx.store, ctx.delimited_k)
        return forward()

    return (yield WithHandler(handler=ask_handler, expr=program))
```

### Intercept → `with_intercept()`

```python
# CURRENT: Built-in InterceptFrame
@do
def program():
    result = yield Intercept(
        transform=lambda eff: TransformedEffect(eff),
        program=inner()
    )
    return result

# TARGET: User-space implementation
@do
def with_intercept(
    transform: Callable[[EffectBase], EffectBase],
    program: Program[T]
) -> Program[T]:
    """Transform effects before they reach outer handlers."""
    def intercept_handler(effect: EffectBase, ctx: HandlerContext) -> Program[CESKState]:
        transformed = transform(effect)
        @do
        def forward():
            result = yield transformed
            return CESKState.with_value(result, ctx.env, ctx.store, ctx.delimited_k)
        return forward()

    return (yield WithHandler(handler=intercept_handler, expr=program))
```

## Scheduler Effect Implementations

### Gather → `_SchedulerGather`

```python
# CURRENT: Built-in GatherFrame
@do
def program():
    results = yield Gather([task1(), task2(), task3()])
    return results

# TARGET: Scheduler effect
@do
def gather(programs: list[Program[T]]) -> Program[list[T]]:
    """Run programs concurrently, collect all results."""
    return (yield _SchedulerGather(programs=programs))

# Scheduler handler implementation
def scheduler_handler(effect: EffectBase, ctx: HandlerContext) -> Program[CESKState]:
    if isinstance(effect, _SchedulerGather):
        # Create child task for each program
        task_ids = [scheduler.create_task(p) for p in effect.programs]
        # Block current task until all children complete
        scheduler.block_on_all(ctx.task_id, task_ids)
        # When unblocked, scheduler provides results
        return Program.pure(CESKState.blocked(...))
    ...
```

### Race → `_SchedulerRace`

```python
# CURRENT: Built-in RaceFrame
@do
def program():
    winner = yield Race([fast_task(), slow_task()])
    return winner

# TARGET: Scheduler effect
@do
def race(programs: list[Program[T]]) -> Program[T]:
    """Run programs concurrently, return first to complete."""
    return (yield _SchedulerRace(programs=programs))

# Scheduler handler cancels losers when winner completes
```

## Architectural Benefits

### 1. Simplicity

```
BEFORE: 8+ Frame types, each with on_value/on_error logic
AFTER:  2 Frame types (ReturnFrame, HandlerFrame)
```

### 2. User Extensibility

Users can define custom scoped behaviors:

```python
# User-defined: Transaction with rollback
@do
def with_transaction(program: Program[T]) -> Program[T]:
    @do
    def tx_handler(effect, ctx):
        if isinstance(effect, DBWrite):
            buffer.append(effect)
            return CESKState.with_value(None, ...)
        return forward(effect)

    try:
    result = yield WithHandler(handler=tx_handler, expr=program)
        commit(buffer)
        return result
    except Exception as e:
        rollback(buffer)
        raise

# User-defined: Timeout wrapper
@do
def with_timeout(seconds: float, program: Program[T]) -> Program[T]:
    @do
    def timeout_handler(effect, ctx):
        if isinstance(effect, _CheckTimeout):
            if elapsed() > seconds:
                raise TimeoutError()
            return CESKState.with_value(None, ...)
        return forward(effect)

    return (yield WithHandler(handler=timeout_handler, expr=program))
```

### 3. Clear Separation of Concerns

```
┌─────────────────────────────────────────────────────────────────┐
│  RESPONSIBILITY MATRIX                                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  step.py:                                                       │
│    - Generator advancement                                      │
│    - Value/Error propagation through K                          │
│    - Effect extraction → return to runtime                      │
│                                                                 │
│  HandlerFrame:                                                  │
│    - Effect interception (via handler function)                 │
│    - Environment restoration on completion                      │
│                                                                 │
│  ReturnFrame:                                                   │
│    - @do generator boundary marker                              │
│    - Continuation for generator results                         │
│                                                                 │
│  Scheduler (handler):                                           │
│    - Task creation/management                                   │
│    - Blocking conditions                                        │
│    - Gather/Race coordination                                   │
│    - Time management                                            │
│                                                                 │
│  User-space helpers:                                            │
│    - Safe (error recovery)                                      │
│    - Listen (log capture)                                       │
│    - Local (scoped env)                                         │
│    - Intercept (effect transform)                               │
│    - Custom patterns                                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Performance Considerations

The overhead of user-space implementations vs built-in Frames is minimal:

| Operation | Built-in Frame | User-space Handler |
|-----------|---------------|-------------------|
| Safe error catch | Frame.on_error | Python try-except |
| Listen log append | Frame internal | Closure variable |
| Local env lookup | Frame.saved_env | Handler closure |

The dominant costs in CESK execution are:
- Generator creation/iteration
- Store dictionary operations
- Continuation list manipulation

Frame dispatch overhead is negligible in comparison.

## Migration Plan

### Phase 1: Implement User-Space Helpers

Create `doeff/effects/patterns.py`:
- `with_safe(program, default)`
- `with_listen(program)`
- `with_local(env_updates, program)`
- `with_intercept(transform, program)`

### Phase 2: Migrate Built-in Effects

Update `Safe`, `Listen`, `Local`, `Intercept` effects to use user-space helpers internally:

```python
# doeff/effects/control.py
class Safe(EffectBase):
    program: Program[T]
    default: T

    def __init__(self, program, default):
        # Internally use with_safe
        self._impl = with_safe(program, default)
```

### Phase 3: Migrate Gather/Race to Scheduler

Update scheduler handler to handle `_SchedulerGather` and `_SchedulerRace` directly.

### Phase 4: Remove Deprecated Frames

Delete from `doeff/cesk/frames.py`:
- SafeFrame
- ListenFrame
- LocalFrame
- InterceptFrame
- GatherFrame
- RaceFrame

Keep only:
- ReturnFrame
- HandlerFrame

### Phase 5: Update Tests

Ensure all tests pass with the minimal Frame architecture.

## Success Criteria

1. Only 2 Frame types exist: ReturnFrame, HandlerFrame
2. All current functionality preserved
3. Users can define custom scoped behaviors via WithHandler
4. No performance regression in benchmarks
5. All existing tests pass

## Relationship to Other Specs

- **SPEC-CESK-001**: This spec completes the "handlers handle all effects" vision
- **SPEC-CESK-EFFECT-BOUNDARIES**: Runner architecture remains unchanged; Frames are internal

## Appendix: Algebraic Effects Comparison

```
┌─────────────────────────────────────────────────────────────────┐
│  ALGEBRAIC EFFECTS LANGUAGE          DOEFF (TARGET)             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  handle <computation> with {         @do                        │
│                                      def my_scoped():           │
│    // effect clause                      try:                   │
│    effect Print(msg) -> k =>                 result = yield     │
│      log(msg);                               WithHandler(       │
│      resume k(())                                handler=...,   │
│                                                  program=...    │
│    // return clause                          )                  │
│    return x =>                               return result      │
│      (x, get_log())                      except Exception:      │
│                                              return default     │
│    // exception clause                                          │
│    exception e =>                                               │
│      default_value                                              │
│  }                                                              │
│                                                                 │
│  UNIFIED SYNTAX                      PYTHON IDIOMS              │
│                                      (same expressiveness)      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

The target architecture achieves equivalent expressiveness to algebraic effects languages while using familiar Python constructs.
