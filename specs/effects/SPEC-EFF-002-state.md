# SPEC-EFF-002: State Effects Semantics

## Status: Accepted

## Summary

This specification defines the semantics for State effects: `Get`, `Put`, and `Modify`.
State effects provide mutable key-value storage within a program execution.

## Effect Definitions

### Get(key: str) -> T | None

Retrieves the value associated with `key` from the store.

**Behavior:**
- Returns the stored value if the key exists
- Returns `None` if the key does not exist
- Does NOT raise KeyError (deliberately different from `Ask`)

**Rationale for None-return semantics:**
State represents mutable runtime data that may or may not have been initialized.
Returning `None` for missing keys allows programs to use presence-checking patterns:

```python
@do
def counter():
    current = yield Get("counter")
    if current is None:
        current = 0
    yield Put("counter", current + 1)
    return current + 1
```

This differs from `Ask` (Reader effect) which raises `KeyError` because environment
keys are expected to be provided at program initialization and represent configuration.

### Put(key: str, value: T) -> None

Stores `value` under `key` in the store.

**Behavior:**
- Overwrites any existing value for the key
- Returns `None`
- Effect is immediately visible to subsequent `Get` calls

### Modify(key: str, func: Callable[[T | None], T]) -> T

Atomically reads, transforms, and stores a value.

**Behavior:**
- Reads current value (or `None` if missing)
- Applies `func` to get new value
- Stores new value
- Returns new value
- If `func` raises an exception, store is unchanged (atomic)

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
    current = yield Get("counter")
    yield Put("counter", (current or 0) + 1)
    return current

@do
def test_gather_shared_store():
    yield Put("counter", 0)
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

### D1: Get Returns None vs Raising KeyError

**Decision:** Get returns `None` for missing keys.

**Considered alternatives:**
1. Raise `KeyError` (like `Ask`)
2. Return `Option[T]`
3. Require default value parameter

**Rationale:**
- State is typically initialized incrementally during execution
- `None` return allows idiomatic Python patterns (`value or default`)
- Explicit missing-key handling aligns with `dict.get()` semantics
- Different from `Ask` which expects pre-configured environment

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
| Get missing key | `test_get_missing_key_returns_none` |

## References

- Implementation: `doeff/effects/state.py`
- Handlers: `doeff/cesk/handlers/core.py`
- Related spec: SPEC-CESK-001 (Separation of Concerns)
- Related issue: gh#157 (async store snapshot)
- Related issue: gh#175 (this spec)
