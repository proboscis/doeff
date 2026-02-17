# SPEC-EFF-003: Writer Effect Semantics

## Status: Confirmed

## Summary

This spec defines the semantics for Writer effects: `Tell` and `Listen`. Writer effects provide a way to accumulate log messages during program execution and optionally capture them via `Listen`.

## Effect Definitions

### Tell

`Tell` appends a message to the shared writer log.

```python
def Tell(message: object) -> Effect:
    """Append message to the writer log. Returns None."""
```

`Tell` accepts any Python object and stores it unchanged.

### Listen

`Listen` executes a sub-program and captures all log entries produced during its execution.

```python
def Listen(sub_program: Program[T]) -> Effect:
    """Run sub_program and return ListenResult(value, captured_logs)."""
```

Returns `ListenResult`:
```python
@dataclass
class ListenResult(Generic[T]):
    value: T           # The result of the sub-program
    log: BoundedLog    # Log entries captured during sub-program execution
```

## Implementation Details

### Log Storage

Logs are stored in the shared store under the key `"__log__"`:
```python
store["__log__"] = [msg1, msg2, msg3, ...]
```

### Listen Mechanism

When `Listen(sub_program)` is executed:
1. Record current log length as `log_start_index`
2. Push `ListenFrame(log_start_index)` onto continuation stack
3. Execute `sub_program`
4. When sub-program completes, `ListenFrame.on_value()`:
   - Capture `store["__log__"][log_start_index:]`
   - Return `ListenResult(value, captured_logs)`
5. Logs remain in store (not removed)

## Composition Rules

### Listen + Tell

Logs produced within a Listen scope are captured in the result.

```python
@do
def program():
    result = yield Listen(inner())  # inner() does Tell("x"), Tell("y")
    # result.log contains ["x", "y"]
    # result.value contains inner's return value
```

**Status: CONFIRMED**

### Listen + Local

Logs produced within a Local scope (inside Listen) are captured normally.

```python
@do
def program():
    result = yield Listen(
        Local({"key": "value"}, 
              inner()  # inner() does Tell("x")
        )
    )
    # result.log contains ["x"]
```

**Status: CONFIRMED**

### Listen + Try

Logs are preserved even when Try catches an error. The logs accumulated before the error are NOT lost.

```python
@do
def inner():
    yield Tell("before_error")
    raise ValueError("error")

@do
def program():
    result = yield Listen(Try(inner()))
    # result.value is Err(ValueError("error"))
    # result.log contains ["before_error"]  # Log preserved!
```

**Status: CONFIRMED**

### Listen + Gather

When Gather executes multiple programs:
- All programs share the same log store
- Log ordering depends on execution order

| Runtime | Log Ordering |
|---------|--------------|
| SyncRuntime | **Sequential** - logs appear in program order |
| AsyncRuntime | **Non-deterministic** - logs interleave based on scheduling |

```python
@do
def task(name):
    yield Tell(f"{name}_start")
    yield Tell(f"{name}_end")
    return name

@do
def program():
    result = yield Listen(Gather(task("A"), task("B")))
    # SyncRuntime: ["A_start", "A_end", "B_start", "B_end"]
    # AsyncRuntime: Could be ["A_start", "B_start", "A_end", "B_end"] or any interleaving
```

**Status: CONFIRMED (non-deterministic in async)**

### Listen + Listen (Nested)

Nested Listen scopes work independently. Inner Listen captures only inner logs in its ListenResult. However, logs are NOT removed from the store, so:

- Outer Listen sees ALL logs (including logs from inner scope)
- Inner Listen only captures logs from its own scope

```python
@do
def inner():
    yield Tell("inner1")
    yield Tell("inner2")
    return "inner_result"

@do
def middle():
    yield Tell("middle1")
    inner_listen = yield Listen(inner())
    # inner_listen.log = ["inner1", "inner2"]
    yield Tell("middle2")
    return inner_listen

@do
def outer():
    outer_listen = yield Listen(middle())
    # outer_listen.log = ["middle1", "inner1", "inner2", "middle2"]
    # outer_listen.value.log = ["inner1", "inner2"]  (the inner ListenResult)
```

**Semantics:**
- Logs accumulate globally and are never removed
- Each Listen captures from its start point to current
- Nested Listen captures its local scope but doesn't hide from outer Listen

**Status: CONFIRMED**

## Open Questions (Resolved)

### 1. Listen + Gather log ordering

**Resolution:** Non-deterministic in AsyncRuntime (depends on execution order). Sequential in SyncRuntime.

This is intentional - parallel execution means parallel logging. If deterministic ordering is required, use sequential execution or sort logs by timestamp.

### 2. Listen + Try (logs preserved on error)

**Resolution:** Yes, logs are preserved. Try only catches the error and converts to Err result; it does not touch the log store.

### 3. Nested Listen

**Resolution:** Outer Listen sees all logs including inner. Inner Listen captures only its local scope. Logs are never removed from store.

### 4. Log removed in favor of Tell

**Resolution:** `Log` has been removed. Use `Tell` for all writer effect needs.

- `Tell` - append any object to the log
- `StructuredLog` / `slog` - convenience for `Tell({**kwargs})`

## Additional Effects

### StructuredLog / slog

Convenience for logging structured data:

```python
def StructuredLog(**entries: object) -> Effect:
    """Log a dictionary of key-value pairs."""
    # Equivalent to: Tell({"key1": val1, "key2": val2, ...})

def slog(**entries: object) -> WriterTellEffect:
    """Lowercase alias for StructuredLog."""
```

## Test Matrix

| Composition | Tested | File |
|-------------|--------|------|
| Listen + Tell | Yes | `tests/effects/test_effect_combinations.py` |
| Listen + Local | Yes | `tests/effects/test_effect_combinations.py` |
| Listen + Try (success) | Yes | `tests/effects/test_effect_combinations.py` |
| Listen + Try (error) | Yes | `tests/effects/test_effect_combinations.py` |
| Listen + Gather (sync) | Yes | `tests/effects/test_effect_combinations.py` |
| Listen + Gather (async) | Yes | `tests/effects/test_effect_combinations.py` |
| Listen + Listen (nested) | Yes | `tests/effects/test_effect_combinations.py` |

## References

- Effect definitions: `doeff/effects/writer.py`
- Handlers: `doeff/handlers.py`
- ListenFrame: `packages/doeff-vm/src/frame.rs`
- Related issue: [#176](https://github.com/proboscis/doeff/issues/176)
