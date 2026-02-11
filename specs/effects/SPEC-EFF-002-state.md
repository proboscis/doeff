# SPEC-EFF-002: State Effects Semantics

## Status: Accepted

## Summary

This specification defines the semantics for State effects: `Get`, `Put`, and `Modify`.
State effects provide mutable key-value storage within a program execution.

## Effect Definitions

### Get(key: str) -> T

Retrieves the value associated with `key` from the store.

**Behavior:**
- Returns the stored value if the key exists
- Raises `KeyError` if the key does not exist (consistent with `Ask`)

**Rationale for KeyError semantics:**
1. **Consistency with Ask** - Both `Get` and `Ask` are lookup operations; having
   consistent error behavior reduces cognitive load
2. **None is valid data** - If `Get` returned `None` for missing keys, you couldn't
   distinguish between "key not found" and "key exists with value None"
3. **Fail-fast** - Explicit errors catch bugs earlier than silent None propagation
4. **Type safety** - `Get[T]` returns `T`, not `T | None`, avoiding unnecessary
   None-handling when you know the key exists

**Handling missing keys:**
Use `Safe` to handle potentially missing keys:

```python
@do
def counter():
    result = yield Safe(Get("counter"))
    current = result.value_or(0)  # Default to 0 if missing
    yield Put("counter", current + 1)
    return current + 1
```

### Put(key: str, value: T) -> None

Stores `value` under `key` in the store.

**Behavior:**
- Overwrites any existing value for the key
- Returns `None`
- Effect is immediately visible to subsequent `Get` calls

### Modify(key: str, func: Callable[[T | None], T]) -> T

Atomically reads, transforms, and stores a value.

**Behavior:**
- Reads current value, or `None` if key is missing (does NOT raise KeyError)
- Applies `func` to get new value
- Stores new value under the key
- Returns new value
- If `func` raises an exception, store is unchanged (atomic)

**Note:** Unlike `Get`, `Modify` does NOT raise `KeyError` for missing keys.
This enables atomic initialization patterns:

```python
@do
def increment():
    # Atomically initialize to 0 if missing, then increment
    return (yield Modify("counter", lambda x: (x or 0) + 1))
```

## Composition Rules

### Put + Get: Immediate Visibility

State changes are immediately visible within the same execution context.

```python
@do
def test_immediate_visibility():
    yield Put("x", 42)
    value = yield Get("x")
    return value  # Returns 42
```

**Verified by:** `test_put_get_immediate_visibility`

### Safe + Put: Changes Persist on Caught Error

State modifications made before an error occurs persist even when `Safe` catches the error.
There is NO automatic rollback.

```python
@do
def risky_operation():
    yield Put("modified", True)
    raise ValueError("error")

@do
def test_safe_preserves_state():
    yield Put("modified", False)
    result = yield Safe(risky_operation())
    # result is Err(ValueError)
    value = yield Get("modified")
    return value  # Returns True - change persisted
```

**Rationale:**
- Implementing transactional rollback would require significant complexity
- Programs needing transaction semantics can implement explicit compensation
- This behavior is consistent with standard imperative programming expectations

**Verified by:** `test_safe_preserves_state_changes`

### Gather + Put: Shared Store Semantics

In `AsyncRuntime`, all parallel branches share the same store. Modifications in one
branch are visible to other branches and the parent.

```python
@do
def increment():
    current = yield Get("counter")  # Key must exist
    yield Put("counter", current + 1)
    return current

@do
def test_gather_shared_store():
    yield Put("counter", 0)  # Initialize before Gather
    results = yield Gather(increment(), increment(), increment())
    final = yield Get("counter")
    return (results, final)  # Returns ([0, 1, 2], 3)
```

**Note:** Due to concurrent execution, results may vary based on scheduling order.
The guarantee is that all modifications are visible and no updates are lost.

**Known Issue:** gh#157 documents stale snapshot behavior in some edge cases.

**Verified by:** `test_gather_shared_store_semantics`

### Modify: Atomic Updates

`Modify` provides atomic read-modify-write semantics. If the transform function
raises an exception, the store remains unchanged.

```python
@do
def test_modify_atomicity():
    yield Put("value", 10)
    
    def failing_transform(x):
        raise ValueError("transform failed")
    
    try:
        yield Modify("value", failing_transform)
    except ValueError:
        pass
    
    value = yield Get("value")
    return value  # Returns 10 - unchanged
```

**Verified by:** `test_modify_atomic_on_error`

## Design Decisions

### D1: Get Raises KeyError for Missing Keys

**Decision:** Get raises `KeyError` for missing keys (consistent with `Ask`).

**Considered alternatives:**
1. Return `None` for missing keys
2. Return `Option[T]`
3. Require default value parameter

**Rationale:**
- **Consistency**: Both `Get` and `Ask` are lookups; same error behavior is intuitive
- **None ambiguity**: Returning None cannot distinguish "not found" from "found None"
- **Fail-fast**: Explicit errors catch bugs earlier than silent None propagation
- **Type safety**: `Get[T]` returns `T`, not `T | None`

**Handling missing keys:** Use `Safe(Get(...))` or `Modify` for initialization patterns.

### D2: No Transaction Rollback on Error

**Decision:** State changes persist through `Safe` error boundaries.

**Considered alternatives:**
1. Automatic rollback on Safe-caught errors
2. Explicit `Transaction` effect wrapper
3. Copy-on-write semantics for Safe blocks

**Rationale:**
- Complexity of implementing true transaction semantics
- Most use cases don't require rollback
- Explicit compensation patterns are clearer when needed
- Consistent with imperative programming mental model

### D3: Shared Store for Gather

**Decision:** All Gather branches share the same store.

**Considered alternatives:**
1. Snapshot isolation (each branch gets copy)
2. Merge strategy on completion
3. Separate stores with explicit sync points

**Rationale:**
- Simpler mental model for users
- Enables coordination between parallel branches
- Snapshot isolation would require merge logic with conflict resolution
- Users needing isolation can use explicit local state

## Test Coverage

All composition rules are verified by tests in `tests/effects/test_state_semantics.py`:

| Rule | Test |
|------|------|
| Put + Get | `test_put_get_immediate_visibility` |
| Safe + Put | `test_safe_preserves_state_changes` |
| Gather + Put | `test_gather_shared_store_semantics` |
| Modify atomicity | `test_modify_atomic_on_error` |
| Modify missing key | `test_modify_missing_key_receives_none` |
| Get missing key | `test_get_missing_key_raises_keyerror` |

## References

- Implementation: `doeff/effects/state.py`
- Handlers: `doeff/handlers.py`
- Related issue: gh#157 (async store snapshot)
- Related issue: gh#175 (this spec)
