# SPEC-EFF-004: Control Effects Semantics

## Status: Draft

## Summary

This specification defines the semantics for Control effects in doeff: `Pure`, `Safe`, and `Intercept`. These are foundational effects that control program execution flow, error handling, and effect transformation.

---

## Core Design: EffectValue and DoExpr are separated

In doeff, **Effect is data (EffectValue), not Program control**. Runtime dispatch is explicit through `Perform(effect)`:

```python
@do
def example():
    x = yield Ask("key")      # lowered to Perform(Ask("key"))
    y = yield some_kleisli()   # lowered to Perform(KPC(...))
    return x + y
```

**Implications:**

1. **Uniform `yield` syntax**: Users can `yield Ask(...)`, but runtime lowers to `Perform(effect)`.
2. **Explicit control IR**: Composition methods (`map`, `flat_map`) live on DoExpr control nodes.
3. **Effect payload semantics**: Effects are handler payload data, not executable control nodes.

```python
# Composition/interception on DoExpr control nodes:
Perform(Ask("key")).map(str.upper)
some_kleisli().map(str.upper)
Intercept(Perform(Ask("key")), transform)
Intercept(some_kleisli(), transform)
```

**`intercept` is syntactic sugar:**

```python
program.intercept(transform)
# is sugar for:
Intercept(program, transform)
```

**InterceptFrame is THE unified mechanism:**

```
Intercept(program, transform)
  → handler pushes InterceptFrame(transform) onto K
  → executes program
  → ANY effect yielded passes through InterceptFrame
  → transform is applied (first non-None wins)
```

All interception flows through InterceptFrame. There is no separate "structural" or "compile-time" interception path.

**Runtime requirement for child propagation:**

For interception to reach children (Gather branches, Spawn tasks, etc.), runtimes MUST propagate InterceptFrame(s) to child execution contexts. This is a runtime implementation requirement, not automatic.

---

## Intercept Semantics (Detailed)

### Transform Function Contract

```python
def transform(effect: Effect) -> Effect | Program | None:
    ...
```

| Return Value | Behavior |
|--------------|----------|
| `None` | Passthrough - try next transform, or use original effect |
| `Effect` | Substitute - use this effect instead (NOT re-transformed) |
| `Program` | Replace - execute this program instead of the effect |

### Key Semantic Rules

**1. No re-transformation of returned Effects:**

When a transform returns a substitute Effect, that Effect is used directly without re-entering the same transform chain. This prevents infinite loops.

```python
def transform(e):
    if isinstance(e, AskEffect):
        return Ask("other_key")  # Returns new AskEffect
    return None

# Ask("other_key") is NOT passed through this transform chain again
# It goes directly to the handler (or outer InterceptFrames in K)
```

**Precise scope:** The returned Effect skips remaining transforms in this InterceptFrame. It may still pass through outer InterceptFrames higher in K.

**2. Replacement Programs ARE intercepted:**

When a transform returns a Program, that Program executes under the current intercept scope. Effects yielded by the replacement Program WILL pass through the InterceptFrame.

```python
def transform(e):
    if isinstance(e, AskEffect):
        return some_program()  # Replacement Program
    return None

# some_program() executes, its yielded effects ARE intercepted
# Can cause infinite loops if some_program yields Ask - user responsibility
```

**3. Interception always propagates to children:**
```python
Gather(child1, child2).intercept(f)
Safe(inner_program).intercept(f)
Spawn(background_task).intercept(f)

# In ALL cases, f sees effects from children/inner/background
# There is no "shallow" interception
```

**4. Spawn/background tasks inherit InterceptFrame:**
```python
@do
def program():
    yield Spawn(background_task())  # background_task sees InterceptFrame
    return "done"

program.intercept(f)  # f intercepts effects from background_task too
```

**5. Parallel ordering is undefined:**
```python
Gather(task_a, task_b).intercept(f)
# If task_a and task_b yield effects concurrently,
# the order f sees them is NOT guaranteed
```

**6. First non-None wins in chained intercepts:**
```python
program.intercept(f).intercept(g)
# Transforms accumulate as (f, g)
# For each effect: try f first, if None try g, if None use original
```

---

## Effect Definitions

### 1. Pure Effect

**Module:** `doeff/effects/pure.py`

```python
@dataclass(frozen=True)
class PureEffect(EffectBase):
    value: Any
```

**Semantics:**
- `Pure(value)` represents an immediate value without performing any side effect
- This is the "return" case of the Free monad - lifts a value into the effect system
- When executed, immediately returns `value` without any state changes

**Handler Behavior:**
```python
def handle_pure(effect: PureEffect, task_state, store) -> ContinueValue:
    return ContinueValue(
        value=effect.value,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )
```

**Key Properties:**
- `yield Pure(x)` is equivalent to returning `x` directly in most contexts
- `Pure` does not modify state, environment, or log
- `Pure.intercept(transform)` returns self (no nested effects to transform)

---

### 2. Safe Effect

**Module:** `doeff/effects/result.py`

```python
@dataclass(frozen=True)
class ResultSafeEffect(EffectBase):
    sub_program: ProgramLike
```

**Semantics:**
- `Safe(program)` executes `program` and catches any exceptions
- Returns `Ok(value)` on success, `Err(exception)` on failure
- **NO ROLLBACK**: State changes and log entries from `program` persist even on error

**Handler Behavior:**
```python
def handle_safe(effect: ResultSafeEffect, task_state, store) -> FrameResult:
    return ContinueProgram(
        program=effect.sub_program,
        env=task_state.env,
        store=store,
        k=[SafeFrame(task_state.env)] + task_state.kontinuation,
    )
```

**Frame Behavior:**

| Scenario | Frame Action |
|----------|--------------|
| Sub-program returns value | Wrap in `Ok(value)`, continue with value |
| Sub-program throws exception | Wrap in `Err(exception)`, continue with value (NOT error) |

**Key Properties:**

1. **State Persistence on Error:**
   ```python
   @do
   def program():
       yield Put("counter", 0)
       result = yield Safe(failing_program_that_increments_counter())
       # counter is now 1 even though failing_program raised an error
       counter = yield Get("counter")  # Returns 1, not 0
   ```

2. **Log Persistence on Error:**
   ```python
   @do
   def program():
       result = yield Safe(program_that_logs_then_fails())
       # Logs from program_that_logs_then_fails ARE preserved
   ```

3. **Environment Scoping:**
   - Safe captures the environment at creation
   - On completion (success or error), environment is restored to the captured state

**Design Decision: No SafeTx (No Rollback)**

**Current Behavior:** State and logs persist on error (NO rollback).

**Rationale:**
- Simpler implementation - no snapshot/restore mechanism needed
- Matches Python's exception handling semantics (side effects are not rolled back)
- Users who need rollback can implement it explicitly with state snapshots
- Async/parallel execution makes rollback semantically complex

**Alternative Not Implemented:** `SafeTx` with transactional rollback was considered but not implemented because:
1. It would require snapshotting the entire store before execution
2. Log rollback is ambiguous (should diagnostic logs be rolled back?)
3. Cache operations and IO effects cannot be rolled back
4. Adds complexity without clear benefit over explicit snapshot patterns

---

### 3. Intercept Effect

**Module:** `doeff/effects/intercept.py`

```python
@dataclass(frozen=True)
class InterceptEffect(EffectBase):
    program: Program
    transforms: tuple[Callable[[Effect], Effect | Program | None], ...]
```

**Handler Behavior:**
```python
def handle_intercept(effect: InterceptEffect, task_state, store) -> FrameResult:
    return ContinueProgram(
        program=effect.program,
        env=task_state.env,
        store=store,
        k=[InterceptFrame(effect.transforms)] + task_state.kontinuation,
    )
```

**Key Properties:**

1. **Values pass through:** Results pass through InterceptFrame unchanged
2. **Errors pass through:** Exceptions propagate, not transformed
3. **Transform exceptions propagate:** If transform throws, execution fails

See **Intercept Semantics (Detailed)** section above for full rules.

---

## Composition Rules

### Safe + Local

**Rule:** Environment is restored even on caught error.

```python
@do
def test_safe_local_env_restored():
    original = yield Ask("key")  # Returns "original"
    result = yield Safe(Local({"key": "modified"}, failing_program()))
    after = yield Ask("key")  # Returns "original" (restored)
    return (original, result, after)
```

**Semantics:**
- Local creates LocalFrame to restore environment
- Safe creates SafeFrame for error catching
- On error: SafeFrame catches error, then LocalFrame restores env
- Order matters: `Safe(Local(...))` vs `Local(Safe(...))`

### Safe + Put

**Rule:** State persists on caught error.

```python
@do
def test_safe_put_state_persists():
    yield Put("counter", 0)
    result = yield Safe(increment_then_fail())  # Increments counter then fails
    counter = yield Get("counter")  # Returns 1 (persisted)
    return (result.is_err(), counter)  # (True, 1)
```

**Semantics:**
- State changes made before the error ARE preserved
- This matches Python's exception semantics (no automatic rollback)
- If rollback is needed, use explicit snapshotting

### Nested Safe

**Rule:** Inner Safe catches first.

```python
@do
def test_nested_safe():
    result = yield Safe(
        Safe(failing_program())  # Inner Safe catches
    )
    # result is Ok(Err(exception)), not Err(exception)
```

**Semantics:**
- Inner Safe converts exception to `Err(exception)`
- Outer Safe sees successful completion (the `Err` value)
- Outer Safe wraps in `Ok(Err(exception))`

### Intercept + Intercept

**Rule:** Transforms accumulate; first non-None wins.

```python
program.intercept(f).intercept(g)
# Transforms: (f, g)
# For each effect: try f, if None try g, if None use original
```

### Intercept + Gather/Safe/Spawn

**Rule:** Interception ALWAYS propagates to children.

```python
Gather(child1, child2).intercept(f)
# f sees effects from child1 AND child2

Safe(inner).intercept(f)
# f sees effects from inner

Spawn(background).intercept(f)
# f sees effects from background task
```

**Note:** The container effect (GatherEffect, SafeEffect, etc.) itself is consumed by its handler. InterceptFrame sees effects *from within* the container.

---

## Extensibility: Custom Frames

Users can define custom control effects with custom Frames **without modifying the runtime**.

### The Frame Protocol

```python
@runtime_checkable
class Frame(Protocol):
    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        """Handle value passing through this frame."""
        ...

    def on_error(
        self,
        error: BaseException,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        """Handle error passing through this frame."""
        ...
```

### Handler Control Options

| Primitive | When to use |
|------|-------------|
| `yield Resume(k, value)` | Resume continuation with a value |
| `yield Delegate()` | Delegate effect handling to the outer handler |
| `yield <Program>` | Execute a nested program in handler logic |

### Example: Custom Timeout Effect

```python
from dataclasses import dataclass
from doeff import Delegate, EffectBase, Resume, do

# 1. Define the Effect
@dataclass(frozen=True)
class TimeoutEffect(EffectBase):
    program: Program
    timeout_seconds: float

# 2. Define the Handler
@do
def handle_timeout(effect, k):
    if isinstance(effect, TimeoutEffect):
        # Add timeout policy around nested execution in normal Python code.
        # (details omitted for brevity)
        result = yield effect.program
        return (yield Resume(k, result))
    yield Delegate()

# 4. Register with Runtime
runtime = AsyncRuntime(handlers={
    **default_handlers(),
    TimeoutEffect: handle_timeout,
})

# 5. Use it
@do
def my_program():
    result = yield TimeoutEffect(slow_operation(), timeout_seconds=5.0)
    return result
```

### Extension Points Summary

| Extension | How |
|-----------|-----|
| New Effect type | Subclass `EffectBase` |
| New Frame type | Implement `Frame` protocol (`on_value`, `on_error`) |
| New Handler | Function returning `FrameResult`, push Frame onto K |
| Register | Pass `handlers={..., MyEffect: my_handler}` to Runtime |

**Runtime guarantee:** The runtime dispatches ALL continuation frames through the `Frame` protocol (`on_value`/`on_error`), not just built-in frames. This makes custom Frames first-class citizens.

This allows implementing custom control flow (transactions, timeouts, retries, resource management) without modifying doeff internals.

---

## Implementation Notes

### Files Involved

| File | Purpose |
|------|---------|
| `doeff/effects/pure.py` | PureEffect definition |
| `doeff/effects/result.py` | ResultSafeEffect definition |
| `doeff/effects/intercept.py` | InterceptEffect definition |
| `doeff/cesk/frames.py` | SafeFrame, InterceptFrame, LocalFrame |
| `doeff/cesk/handlers/control.py` | handle_safe, handle_intercept, handle_local |
| `doeff/cesk/helpers.py` | apply_intercept_chain |
| `doeff/cesk/step.py` | Effect processing and frame handling |

### Handler Registration

All control handlers are registered in `doeff/cesk/handlers/__init__.py`:

```python
return {
    PureEffect: handle_pure,
    LocalEffect: handle_local,
    ResultSafeEffect: handle_safe,
    InterceptEffect: handle_intercept,
    # ...
}
```

---

## Design Decisions (Resolved)

### 1. Safe rollback semantics
- **Decision:** NO rollback - state/logs persist on error
- **Rationale:** Simpler, matches Python semantics, rollback is complex in async contexts

### 2. Intercept composition order
- **Decision:** `p.intercept(f).intercept(g)` - `f` applies first (first non-None wins)
- **Implementation:** Transforms accumulate in tuple, applied in order

### 3. Intercept + Gather/Safe/Spawn scope
- **Decision:** Interception ALWAYS propagates to children
- **Mechanism:** Unified via InterceptFrame - all child effects pass through parent's InterceptFrame
- **No shallow option:** There is no way to intercept "only this level"
- **Implementation:** Runtime must propagate InterceptFrame to child tasks

### 4. Transform returns Effect
- **Decision:** Returned Effect is NOT re-transformed
- **Rationale:** Prevents infinite loops where transform triggers itself

### 5. Transform returns Program
- **Decision:** Returned Program replaces the original effect
- **Semantics:** The Program is executed, original effect is NOT executed

### 6. Spawn/background task inheritance
- **Decision:** Background tasks inherit InterceptFrame from parent
- **Rationale:** Consistent behavior - intercept wraps ALL effects in its scope

### 7. Parallel ordering
- **Decision:** Order of interception in parallel contexts is UNDEFINED
- **Rationale:** Guaranteeing order would require synchronization overhead

---

## References

- Issue: [gh#177](https://github.com/CyberAgentAILab/doeff/issues/177)
- CESK Architecture: `specs/cesk-architecture/SPEC-CESK-001-separation-of-concerns.md`
- Program Architecture: `specs/program-architecture/architecture.md`
