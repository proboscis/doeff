# SPEC-EFF-001: Reader Effects (Ask, Local)

**Status:** Confirmed  
**Reference:** gh#174  
**Test Coverage:** `tests/effects/test_reader_effects.py`

## Overview

Reader effects provide read-only environment access through `Ask` and scoped environment modification through `Local`. These effects implement the Reader monad pattern from functional programming.

## Effect Definitions

### Ask

```python
Ask(key: EnvKey) -> Effect
```

Looks up the environment entry for `key` and yields the resolved value.

### Local

```python
Local(env_update: Mapping[Any, object], sub_program: ProgramLike) -> Effect
```

Runs a sub-program against an updated environment and yields its value.

## Semantics

### 1. Ask on Missing Key

**Behavior:** Raises `MissingEnvKeyError` when the requested key is not present in the environment.

```python
from doeff import MissingEnvKeyError

@do
def program():
    value = yield Ask("missing_key")  # Raises MissingEnvKeyError
    return value

# MissingEnvKeyError: Environment key not found: 'missing_key'
# Hint: Provide this key via `env={'missing_key': value}` or wrap with `Local({'missing_key': value}, ...)`
```

**Error Attributes:**
- `key`: The environment key that was not found
- Inherits from `KeyError` for backwards compatibility

**Rationale:** 
- Explicit is better than implicit - missing keys indicate configuration errors
- Dedicated error type provides helpful hints for debugging
- Error message explains how to fix the issue
- `KeyError` subclass maintains backwards compatibility with existing `except KeyError:` handlers

**Alternative Considered:** Supporting `Ask(key, default=None)` was considered but rejected to maintain simplicity and encourage explicit environment setup.

### 2. Local + Ask Composition

**Behavior:** Ask inside Local sees the overridden value; environment is restored after Local completes.

```python
@do
def program():
    before = yield Ask("key")        # "original"
    inner = yield Local({"key": "overridden"}, inner_program())  # "overridden"
    after = yield Ask("key")         # "original" (restored)
    return (before, inner, after)
```

**Key Properties:**
- Local creates a new environment by merging `env_update` with the current environment
- Inner overrides take precedence over outer values
- Environment is restored even on error paths (see Local + Safe)
- Keys added by Local are not visible after Local completes

### 3. Local + Local (Nested) Composition

**Behavior:** Inner Local overrides outer, both restore independently.

```python
@do
def program():
    before = yield Ask("key")  # "original"
    result = yield Local({"key": "outer"}, middle())  # -> middle sees "outer"
    after = yield Ask("key")   # "original"
    # Inside middle, another Local({"key": "inner"}) will see "inner"
    # After inner Local completes, middle sees "outer" again
```

**Key Properties:**
- Each Local pushes a new frame onto the continuation stack
- Frames are unwound in LIFO order
- Different keys can be overridden at different levels

### 4. Local + Safe Composition

**Behavior:** Environment is restored even when Safe catches an error.

```python
@do
def program():
    before = yield Ask("key")  # "original"
    safe_result = yield Safe(Local({"key": "in_local"}, failing_inner()))
    after = yield Ask("key")   # "original" (restored even after error)
```

**Key Properties:**
- `LocalFrame.on_error()` restores environment before propagating error
- Safe catches the error after environment restoration
- No environment leakage on error paths

### 5. Local + State Interaction

**Behavior:** State changes inside Local persist outside; Local only scopes environment, not state.

```python
@do
def inner():
    yield Put("counter", 42)
    return "done"

@do
def program():
    before = yield Get("counter")  # 0
    yield Local({"key": "value"}, inner())
    after = yield Get("counter")   # 42 (persists!)
```

**Key Properties:**
- `Local` scopes `env` (Ask) but NOT `store` (Get/Put/Modify)
- State is shared across all scopes
- This is intentional: Reader for read-only config, State for mutable data

**Design Decision:** State persistence through Local boundaries is by design. Use `Safe` to isolate state changes if needed.

### 6. Local + Gather Composition

**Behavior:** Children inherit parent environment; child's Local doesn't affect siblings.

```python
@do
def program():
    before = yield Ask("key")  # "parent_value"
    results = yield Gather(
        child_with_local(),     # Uses Local inside, sees "child_override"
        child_normal(),         # Sees "parent_value"
        child_normal(),         # Sees "parent_value" (not affected by sibling)
    )
    after = yield Ask("key")   # "parent_value" (restored)
```

**Key Properties:**
- All Gather children inherit the environment at Gather time
- Each child runs with its own continuation stack
- Child's Local only affects that child's sub-tree
- Parent environment restored after Gather completes
- State (Get/Put) IS shared between children (unlike env)

## Implementation Notes

### Environment Storage

- Environment is stored as `FrozenDict` for immutability
- Updates create new dicts: `new_env = task_state.env | FrozenDict(effect.env_update)`

### LocalFrame

The `LocalFrame` type handles environment restoration:

```python
@dataclass(frozen=True)
class LocalFrame:
    restore_env: Environment

    def on_value(self, value, env, store, k_rest) -> ContinueValue:
        return ContinueValue(value=value, env=self.restore_env, store=store, k=k_rest)

    def on_error(self, error, env, store, k_rest) -> ContinueError:
        return ContinueError(error=error, env=self.restore_env, store=store, k=k_rest)
```

### Ask Handler

```python
from doeff.cesk.errors import MissingEnvKeyError

def handle_ask(effect: AskEffect, task_state: TaskState, store: Store) -> FrameResult:
    key = effect.key
    if key not in task_state.env:
        raise MissingEnvKeyError(key)
    value = task_state.env[key]
    return ContinueValue(value=value, env=task_state.env, store=store, k=task_state.kontinuation)
```

## Confirmed Composition Rules

| Composition | Behavior | Status |
|------------|----------|--------|
| Local + Ask | Ask sees override, restored after | Confirmed |
| Local + Local | Inner overrides outer, both restore | Confirmed |
| Local + Safe | Env restored even when Safe catches | Confirmed |
| Local + Gather | Children inherit, child's Local isolated | Confirmed |
| Local + State | State persists outside Local | Confirmed |

## References

- Implementation: `doeff/effects/reader.py`
- Handlers: `doeff/cesk/handlers/core.py`, `doeff/cesk/handlers/control.py`
- Frames: `doeff/cesk/frames.py`
- Tests: `tests/effects/test_reader_effects.py`
