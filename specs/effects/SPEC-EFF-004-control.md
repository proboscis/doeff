# SPEC-EFF-004: Control Effects Semantics

## Status: Draft

## Summary

This specification defines the semantics for Control effects in doeff: `Pure`, `Safe`, and `Intercept`. These are foundational effects that control program execution flow, error handling, and effect transformation.

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

**Semantics:**
- `program.intercept(transform)` applies `transform` to all effects yielded by `program`
- Transform receives each effect and can:
  - Return the same effect (passthrough)
  - Return a different effect (substitution)
  - Return a Program (effect replacement with computation)
  - Return `None` (passthrough to next transform)

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

**Transform Application Order:**

When `p.intercept(f).intercept(g)`:
- Transforms are accumulated: `(f, g)`
- Applied in **left-to-right order**: `f` first, then `g`
- **First non-None wins**: If `f(effect)` returns non-None, `g` is not called

```python
def apply_intercept_chain(K: Kontinuation, effect: Effect) -> Effect | Program:
    current = effect
    for frame in K:
        if isinstance(frame, InterceptFrame):
            for transform in frame.transforms:
                result = transform(current)
                if result is not None:
                    current = result
                    break  # First non-None wins within frame
    return current
```

**Composition Rules:**

1. **Chained Intercepts:**
   ```python
   p.intercept(f).intercept(g)
   # Equivalent to: apply g, then f (outer first, then inner)
   # But within each intercept, transforms are applied left-to-right
   ```

2. **Intercept + Gather Scope:**
   - `Gather(...).intercept(f)` intercepts effects **inside** gathered programs
   - This is because InterceptFrame is pushed onto the continuation stack before Gather executes
   - The `GatherEffect` itself is NOT intercepted (it's consumed by the handler directly)
   - Note: Each child program executes with the InterceptFrame in its continuation

3. **Transform Returns Program:**
   - When transform returns a Program, that Program is executed instead of the effect
   - The original effect is not executed
   - Example: `Ask("key") -> Program.pure("intercepted_value")`

**Key Properties:**

1. **Values Pass Through:**
   - Values (successful results) pass through InterceptFrame unchanged
   - Only effects are transformed

2. **Errors Pass Through:**
   - Errors pass through InterceptFrame unchanged
   - Intercept does not catch or transform exceptions

3. **Transform Exceptions:**
   - If a transform function throws, the exception propagates normally
   - The program execution fails with the transform's exception

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

**Rule:** Transforms accumulate; first non-None wins within each frame.

```python
@do
def test_intercept_composition():
    def f(e):
        if isinstance(e, AskEffect):
            return Program.pure("from_f")
        return None  # Passthrough
    
    def g(e):
        if isinstance(e, AskEffect):
            return Program.pure("from_g")
        return None  # Passthrough
    
    # p.intercept(f).intercept(g)
    # f is checked first, g only if f returns None
    result = yield inner_program().intercept(f).intercept(g)
    # Result: "from_f" (f wins because it's first)
```

**Application Order:**
1. When multiple intercepts are chained, they form a stack of InterceptFrames
2. Effects bubble up through frames in K (continuation stack order)
3. Within each frame, transforms are tried in order until one returns non-None

### Intercept + Gather

**Rule:** Intercept on Gather **DOES** transform children's effects.

```python
@do
def test_intercept_gather_scope():
    def intercept_ask(e):
        if isinstance(e, AskEffect):
            return Program.pure("intercepted")
        return None
    
    @do
    def child():
        return (yield Ask("key"))
    
    # This DOES intercept Ask inside children
    result = yield Gather(child(), child()).intercept(intercept_ask)
    # result: ["intercepted", "intercepted"] - children ARE intercepted
```

**Semantics:**
- InterceptFrame is pushed onto the continuation stack before Gather executes
- When Gather runs children, they inherit the continuation stack including InterceptFrame
- The `GatherEffect` itself is NOT intercepted (handler consumes it directly)
- Effects from children bubble up through the InterceptFrame

**Rationale:**
- Continuation stack is inherited by child executions in the current architecture
- This provides a way to wrap all effects in a computation subtree
- For isolation, use a separate runtime or explicit scoping constructs

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

## Open Questions (Resolved)

### 1. Safe rollback semantics
- **Decision:** NO rollback - state/logs persist on error
- **Rationale:** Simpler, matches Python semantics, rollback is complex in async contexts

### 2. Intercept composition order
- **Decision:** `p.intercept(f).intercept(g)` - `f` applies first (first non-None wins)
- **Implementation:** Transforms accumulate in tuple, applied in order

### 3. Intercept + Gather scope
- **Decision:** `Gather(...).intercept(f)` DOES intercept children's effects
- **Rationale:** InterceptFrame is on the continuation stack inherited by children
- **Note:** The `GatherEffect` itself is NOT intercepted (consumed by handler directly)

### 4. Intercept can return Program
- **Decision:** When transform returns Program, it replaces the original effect
- **Semantics:** The returned Program is executed, original effect is NOT executed

---

## References

- Issue: [gh#177](https://github.com/CyberAgentAILab/doeff/issues/177)
- CESK Architecture: `specs/cesk-architecture/SPEC-CESK-001-separation-of-concerns.md`
- Program Architecture: `specs/program-architecture/architecture.md`
