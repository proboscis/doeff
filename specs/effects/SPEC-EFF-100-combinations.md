# SPEC-EFF-100: Effect Combinations

**Status**: Implemented  
**Issue**: gh#180  
**Authors**: @doeff-team  

## Summary

This specification defines how effects interact when composed. It establishes the
behavioral guarantees and laws that govern effect combinations in the doeff framework.

**Important**: Some behaviors differ between `SyncRuntime` and `AsyncRuntime`. Where
differences exist, they are explicitly noted.

---

## Effect Combination Matrix

This matrix describes how effects behave when nested or composed:

| Outer / Inner | Ask | Get | Put | Log | Try | Local | Listen | Intercept | Gather |
|---------------|-----|-----|-----|-----|------|-------|--------|-----------|--------|
| **Local**     | Scoped | Propagates | Propagates | Propagates | Propagates | Scoped | Propagates | Propagates | Propagates |
| **Listen**    | - | - | - | Captured* | - | - | Nested | - | All captured* |
| **Try**      | - | - | Persists | Persists | Wrapped | Restores | - | - | First error |
| **Intercept** | Transform | Transform | Transform | Transform | Transform | Transform | Transform | Transform | Transform |
| **Gather**    | Inherit | Shared** | Shared** | Merged | Isolated | Inherit | Shared | Propagates | Nested |

*Listen captures logs only on success path; errors propagate without log capture.
**Store sharing behavior differs by runtime (see Law 8).

### Matrix Key

- **Scoped**: Effect operates within a bounded scope, restored after
- **Propagates**: Effect passes through unchanged
- **Captured**: Output is captured by the outer effect (success path only)
- **Persists**: Side effects persist even on error
- **Wrapped**: Result wrapped in outer effect's type
- **Restores**: State restored after inner completion
- **Transform**: Effect may be transformed by outer (including nested programs)
- **Isolated**: Each branch operates independently for error handling
- **Inherit**: Child inherits parent's context (snapshot at invocation time)
- **Shared**: All branches share the same resource
- **Merged**: Outputs are combined from all branches
- **Nested**: Inner effect nests within outer

---

## Composition Laws

The following laws MUST hold for all effect compositions:

### Law 1: Local Restoration Law

> Environment MUST restore after Local scope, regardless of success or error.

```python
@do
def test_law():
    outer_val = yield Ask("key")
    try:
        yield Local({"key": "inner"}, failing_program())
    except:
        pass
    restored_val = yield Ask("key")
    assert outer_val == restored_val  # MUST be true
```

### Law 2: Local Non-State-Scoping Law

> Local does NOT scope state (Get/Put). State changes propagate.

```python
@do
def test_law():
    yield Put("counter", 0)
    yield Local({}, do_increment())
    counter = yield Get("counter")
    assert counter == 1  # State change persists
```

### Law 3: Listen Capture Law

> Log/Tell operations within Listen scope are captured ONLY on successful completion.
> On error, the error propagates and logs are NOT wrapped in ListenResult.

```python
@do
def test_law_success():
    result = yield Listen(do_nested_logs())
    # result.log contains ALL logs from entire sub-tree (success path)

@do
def test_law_error():
    result = yield Try(Listen(failing_with_logs()))
    # result is Err(...), logs are NOT captured in ListenResult
    # (error propagated through ListenFrame without capture)
```

### Law 4: Try Non-Rollback Law

> Try does NOT rollback state on error. State changes persist.

```python
@do
def test_law():
    yield Put("counter", 0)
    result = yield Try(do_modify_then_fail())
    # counter is still modified, even though Try caught error
    counter = yield Get("counter")
    assert counter > 0  # State persisted
```

### Law 5: Try Environment Restoration Law

> Try restores environment context (but not state) to pre-Try value.

```python
@do
def test_law():
    # If Local is inside Try and fails, env still restored
    yield Try(Local({"x": 1}, failing_program()))
    # Environment is restored to pre-Try state
```

### Law 6: Intercept Transformation Law

> Intercept transforms effects within the intercepted program, INCLUDING effects
> from nested program values stored in effect payloads (e.g., Gather children,
> Local sub-programs, Listen sub-programs).

This occurs via structural rewriting: when `program.intercept(transform)` is called,
the transform is applied recursively to nested Programs within effect payloads.

```python
@do
def test_law():
    transform_count = [0]
    
    def counting_transform(effect):
        if isinstance(effect, AskEffect):
            transform_count[0] += 1
        return effect
    
    @do
    def child():
        return (yield Ask("key"))
    
    # Intercept DOES affect Gather children via structural rewriting
    result = yield Gather(child(), child()).intercept(counting_transform)
    assert transform_count[0] == 2  # Both children's Ask effects transformed
```

**Note**: Intercept scope across task boundaries (spawned tasks that don't share
the parent continuation) is runtime-dependent.

### Law 7: Gather Environment Inheritance Law

> Gather children inherit parent's environment as a snapshot at Gather invocation time.
> Environment changes in one child do NOT affect other children.

```python
@do
def test_law():
    yield Local({"env_key": "value"}, Gather(
        child_that_reads_env_key()  # sees "value"
    ))
```

### Law 8: Gather Store Sharing Law (Runtime-Dependent)

Store sharing behavior differs between runtimes:

#### Law 8a: SyncRuntime (Sequential Gather)

> Gather children execute sequentially and share store deterministically.
> Each child observes state changes from previous children.

```python
@do
def test_law_sync():
    yield Put("counter", 0)
    # Sequential: child1 runs, then child2, then child3
    results = yield Gather(increment(), increment(), increment())
    # results == [0, 1, 2] (deterministic ordering)
    final = yield Get("counter")
    assert final == 3
```

#### Law 8b: AsyncRuntime (Parallel Gather)

> Gather children execute in parallel with a shared store object.
> Concurrent writes may race; ordering is non-deterministic.
> Users MUST NOT rely on read-modify-write patterns without explicit coordination.

```python
@do
def test_law_async():
    yield Put("counter", 0)
    # Parallel: all children may run concurrently
    results = yield Gather(increment(), increment(), increment())
    # results order matches input order, but execution interleaves
    # Final counter value is 3, but intermediate observations vary
    final = yield Get("counter")
    assert final == 3  # Eventually consistent
    # BUT: sorted(results) may NOT be [0, 1, 2] due to race conditions
```

**Warning**: AsyncRuntime Gather with shared mutable state is subject to race
conditions. For deterministic behavior, use coordination effects or isolated state.

---

## Error Propagation in Gather

### Law 9: Gather Error Propagation Law

> When a Gather child fails, the error propagates to the parent.
> Other children's behavior on sibling failure is runtime-dependent.

#### SyncRuntime
Sequential execution stops at first error. Subsequent children do not execute.

#### AsyncRuntime
All children are spawned concurrently. On first child failure:
- Parent receives the error
- Other children may continue running (not cancelled)
- Side effects from other children may still occur

```python
@do
def test_law():
    result = yield Try(Gather(
        succeeds_with_side_effect(),
        fails_immediately(),
        succeeds_with_side_effect(),
    ))
    # result.is_err() == True
    # Side effects from children 1 and 3 may or may not have occurred
```

---

## Test Matrix

| Test Name | Tests Law | Status |
|-----------|-----------|--------|
| `test_local_restores_env_on_success` | Law 1 | Implemented |
| `test_local_restores_env_on_error` | Law 1 | Implemented |
| `test_nested_local_override_and_restore` | Law 1 | Implemented |
| `test_local_does_not_scope_state` | Law 2 | Implemented |
| `test_listen_captures_logs_from_local` | Law 3 | Implemented |
| `test_listen_captures_all_gather_logs` | Law 3 | Implemented |
| `test_nested_listen_separation` | Law 3 | Implemented |
| `test_listen_does_not_capture_on_error` | Law 3 | Implemented |
| `test_safe_does_not_rollback_state` | Law 4 | Implemented |
| `test_nested_safe_innermost_catches` | Law 4 | Implemented |
| `test_safe_with_local_restores_env` | Law 5 | Implemented |
| `test_intercept_transforms_gather_children` | Law 6 | Implemented |
| `test_gather_children_inherit_local_env` | Law 7 | Implemented |
| `test_sync_gather_sequential_store_sharing` | Law 8a | Implemented |
| `test_async_gather_parallel_execution` | Law 8b | Implemented |
| `test_gather_error_propagation` | Law 9 | Implemented |
| `test_nested_gather_parallelism` | Integration | Implemented |
| `test_complex_safe_local_listen_combination` | Integration | Implemented |

---

## Design Decisions

### D1: Intercept Scope

**Decision**: Intercept transforms effects within the intercepted program, including
nested program values in effect payloads (Gather children, Local/Listen sub-programs).

**Rationale**: Structural rewriting via `program.intercept(transform)` recursively
applies transforms to nested Programs. This provides consistent transformation
behavior across effect boundaries.

**Limitation**: Intercept cannot transform effects in independently spawned tasks
that don't share the parent continuation stack.

### D2: Listen + Gather Ordering

**Decision**: No ordering guarantees for logs from parallel Gather branches.

**Rationale**: Parallel execution is inherently non-deterministic. Logs are merged
in execution order, which may vary.

### D3: Try Rollback

**Decision**: No transactional rollback for Try. Only environment is restored.

**Rationale**: Rolling back state would require transaction semantics that add
significant complexity. Users requiring transactions should use explicit
checkpoint/restore patterns or database transactions.

### D4: Gather Store Semantics

**Decision**: Store is shared among Gather children in both runtimes.

- **SyncRuntime**: Sequential execution, deterministic state observation
- **AsyncRuntime**: Parallel execution, shared store with race hazards

**Rationale**: Sharing simplifies implementation and matches common use cases.
Users requiring isolation should use explicit state partitioning or future
isolated Gather variants (see gh#157).

### D5: Listen Error Behavior

**Decision**: Listen does NOT capture logs on error path.

**Rationale**: `ListenFrame.on_error` propagates the error without wrapping.
This avoids complexity of combining error and log capture. Users needing
logs from failing computations should use `Try(Listen(...))` pattern and
handle the error case explicitly.

---

## References

- gh#174-178: Individual effect RFCs
- gh#157: Gather store sharing discussion
- `packages/doeff-vm/src/frame.rs`: Frame implementations
- `packages/doeff-vm/src/handler.rs`: Control flow handlers
- `packages/doeff-vm/src/scheduler.rs`: Gather/scheduler handling
- `doeff/rust_vm.py`: `async_run` entrypoint
- `doeff/rust_vm.py`: `run` entrypoint
