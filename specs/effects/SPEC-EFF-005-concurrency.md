# SPEC-EFF-005: Concurrency Effects

## Status: Draft

## Summary

This specification defines the semantics for concurrency effects in doeff: `Gather`, `Await`, `Spawn`, and `Task`. These effects enable parallel and asynchronous program execution within the effect system.

## Effects Overview

| Effect | Purpose | Status |
|--------|---------|--------|
| `Gather` | Execute multiple programs in parallel, collect results | Implemented |
| `Await` | Await an external coroutine/future | Implemented |
| `Spawn` | Spawn a background task, return Task handle | Defined, not implemented |
| `Task.join()` | Wait for spawned task to complete | Defined, not implemented |

---

## Gather Effect

### Definition

```python
@dataclass(frozen=True)
class GatherEffect(EffectBase):
    """Executes all programs in parallel and yields their results as a list."""
    programs: Tuple[ProgramLike, ...]
```

### Basic Semantics

```python
@do
def example():
    results = yield Gather(prog1(), prog2(), prog3())
    # results is [result1, result2, result3] in program order
```

- **Result ordering**: Results are returned in the same order as programs were passed
- **Empty Gather**: `Gather()` returns `[]` immediately
- **Single program**: `Gather(prog)` returns `[result]`

### Store Semantics

**Current behavior: Shared store**

All parallel branches share the same store. Changes made in one branch are visible to other branches and to the parent after Gather completes.

```python
@do
def example():
    yield Put("counter", 0)
    
    @do
    def increment():
        current = yield Get("counter")
        yield Put("counter", current + 1)
        return current
    
    results = yield Gather(increment(), increment(), increment())
    final = yield Get("counter")
    # final == 3 (all increments accumulated)
    # results is [0, 1, 2] (each saw previous state)
```

**Rationale**: Shared store enables coordination between parallel tasks and accumulation of side effects (logs, state updates).

**Known issue**: [gh#157](https://github.com/CyberAgentAILab/doeff/issues/157) may affect async store snapshot behavior.

**Alternative considered**: Isolated snapshots where each child gets a copy of the store at Gather time. This provides better isolation but prevents coordination between tasks.

### Error Handling

**Current behavior: First error fails Gather**

When any parallel program raises an exception, the Gather effect immediately fails with that exception. The exception propagates to the parent context.

```python
@do
def example():
    @do
    def failing():
        raise ValueError("failed")
    
    @do
    def success():
        return "ok"
    
    # First error encountered fails the entire Gather
    result = yield Safe(Gather(success(), failing()))
    # result.is_err() == True
    # result.error is ValueError("failed")
```

**Semantics**:
1. All programs start executing in parallel
2. First exception encountered aborts the Gather
3. Other programs may or may not have completed
4. The failing program's exception is propagated

**Open question**: Should other children be cancelled when one fails?
- Current: No explicit cancellation, but no further results are collected
- Recommendation: No cancellation for consistency (cancellation is complex to implement correctly)

**Open question**: Which error if multiple fail "simultaneously"?
- Current: First error detected by the scheduler wins
- This is implementation-dependent and should not be relied upon

### Environment Inheritance

**Behavior: Children inherit parent environment**

```python
@do
def example():
    config = yield Ask("config")  # Get from parent env
    
    @do
    def child():
        # Same env is accessible
        cfg = yield Ask("config")
        return cfg
    
    results = yield Gather(child(), child())
    # Each child sees the same "config" value
```

Children receive a copy of the parent's environment at Gather time. Environment is read-only (no `Local` equivalent that propagates up).

### Gather + Local

**Behavior: Local changes are scoped to children**

```python
@do
def example():
    @do
    def child_with_local():
        result = yield Local(
            {"override": "value"},
            inner_program()
        )
        return result
    
    results = yield Gather(
        child_with_local(),
        other_child()  # Does not see "override"
    )
```

Each child's `Local` scope is independent. Local environment changes do not leak between parallel branches.

### Gather + Listen

**Behavior: All logs from all children are captured**

```python
@do
def example():
    @do
    def logging_child(name: str):
        yield Log(f"Hello from {name}")
        return name
    
    result = yield Listen(
        Gather(
            logging_child("A"),
            logging_child("B")
        )
    )
    # result.value == ["A", "B"]
    # result.log contains both log messages (order depends on execution)
```

Log entries from all parallel branches are collected. The order of log entries may not match program order (depends on actual execution timing).

### Gather + Safe

**Behavior: First error is wrapped in Result**

```python
@do
def example():
    result = yield Safe(Gather(prog1(), failing_prog(), prog3()))
    # result.is_err() == True
    # result.error contains the exception from failing_prog
```

Safe wraps the entire Gather. If any child fails, the error is captured as `Err(exception)`.

### Gather + Intercept

**Behavior: Parent's intercept DOES apply to children**

```python
@do
def example():
    def transform(effect):
        if isinstance(effect, AskEffect):
            return Pure("intercepted")
        return None
    
    @do
    def child():
        value = yield Ask("key")
        return value
    
    @do
    def gather_programs():
        results = yield Gather(child(), child())
        return results
    
    result = yield intercept_program_effect(
        gather_programs(),
        (transform,)
    )
    # Children's Ask effects ARE intercepted
    # result == ["intercepted", "intercepted"]
```

**Rationale**: Intercept applies to the entire program tree rooted at the intercepted program. Since Gather children are part of that program tree, they receive the same intercept transformations. This provides consistent behavior - when you intercept a program, all effects within it (including nested Gather) are intercepted.

### Nested Gather

**Behavior: Full parallelism at all levels**

```python
@do
def outer():
    results = yield Gather(
        inner_gather_1(),
        inner_gather_2()
    )
    return results

@do
def inner_gather_1():
    results = yield Gather(task_a(), task_b())
    return results

@do
def inner_gather_2():
    results = yield Gather(task_c(), task_d())
    return results
```

All leaf tasks (`task_a`, `task_b`, `task_c`, `task_d`) run in parallel. The nesting structure defines how results are grouped, not how parallelism is limited.

---

## Await Effect

### Definition

```python
@dataclass(frozen=True)
class FutureAwaitEffect(EffectBase):
    """Awaits the given awaitable and yields its resolved value."""
    awaitable: Awaitable[Any]
```

### Semantics

```python
@do
def example():
    result = yield Await(some_async_function())
    return result
```

- Suspends the program until the awaitable completes
- Returns the awaited value
- If the awaitable raises, the exception propagates

### Runtime Behavior

- **AsyncRuntime**: Uses `asyncio.create_task` to schedule the awaitable
- **SyncRuntime**: Not supported (will fail)
- **SimulationRuntime**: May simulate await with controlled time

---

## Spawn Effect (NOT IMPLEMENTED)

### Proposed Definition

```python
@dataclass(frozen=True)
class SpawnEffect(EffectBase):
    """Spawn execution of a program and return a Task handle."""
    program: ProgramLike
    preferred_backend: SpawnBackend | None = None  # "thread", "process", "ray"
    options: dict[str, Any]
```

### Proposed Semantics

```python
@do
def example():
    task = yield Spawn(background_work())
    # Continue immediately, task runs in background
    
    # Later...
    result = yield task.join()  # Wait for completion
```

### Open Questions

#### 1. Store semantics for Spawn

**Option A: Snapshot at spawn time**
- Child gets a copy of the store when spawned
- Changes in child do not affect parent
- Changes in parent do not affect child after spawn

**Option B: Shared store (like Gather)**
- Requires synchronization for thread/process backends
- More complex but enables coordination

**Recommendation**: Snapshot semantics for simplicity and safety.

#### 2. Error handling for background tasks

**Option A: Exception stored in Task, raised on join**
```python
task = yield Spawn(failing_program())
# No error yet
result = yield Safe(task.join())  # Error captured here
```

**Option B: Exception propagates to spawner immediately**
- Harder to implement, breaks "fire and forget" pattern

**Recommendation**: Option A - exceptions stored until join.

#### 3. Cancellation semantics

```python
task = yield Spawn(long_running())
# Later...
yield task.cancel()  # How should this work?
```

**Questions**:
- Should cancel be synchronous or async?
- What happens if task is already completed?
- Should cancelled tasks raise `CancelledError` on join?

**Recommendation**: Follow asyncio conventions:
- `cancel()` is synchronous, requests cancellation
- Task may take time to actually cancel
- `join()` on cancelled task raises `CancelledError`

---

## Task Handle

### Definition

```python
@dataclass(frozen=True)
class Task(Generic[T]):
    """Handle for a spawned task."""
    backend: SpawnBackend
    _handle: Any  # Backend-specific handle
    _env_snapshot: dict[Any, Any]
    _state_snapshot: dict[str, Any]
```

### Methods

| Method | Description |
|--------|-------------|
| `join()` | Returns `TaskJoinEffect` - wait for completion |
| `cancel()` | (Proposed) Request task cancellation |
| `is_done()` | (Proposed) Check if task completed |

---

## Composition Rules Summary

| Composition | Behavior | Test Status |
|-------------|----------|-------------|
| Gather + Local | Children inherit env at spawn; Local in child is scoped | Tested |
| Gather + Put | Shared store; all changes visible | Tested |
| Gather + Listen | All logs captured from all children | Tested |
| Gather + Safe | First error wrapped in Err | Tested |
| Gather + Intercept | Parent intercept DOES apply to children | Tested |
| Nested Gather | Full parallelism at leaf level | Tested |

---

## Implementation Notes

### AsyncRuntime Gather Implementation

The `AsyncRuntime` intercepts `GatherEffect` to implement true parallelism:

1. Create child `TaskState` for each program
2. Add children to scheduler with fresh `TaskId`
3. Parent waits until all children complete
4. Collect results in program order
5. Merge stores (logs, memos, state changes)

See: `doeff/cesk/runtime/async_.py`

### Default Handler (Sequential)

The default handler in `handlers/task.py` executes Gather sequentially using `GatherFrame`. This is used by `SyncRuntime`.

---

## Related Issues

- [gh#157](https://github.com/CyberAgentAILab/doeff/issues/157): Async store snapshot bug
- [gh#156](https://github.com/CyberAgentAILab/doeff/issues/156): AsyncRuntime parallel Gather
- [gh#178](https://github.com/CyberAgentAILab/doeff/issues/178): This spec

---

## References

- Source: `doeff/effects/gather.py`, `doeff/effects/spawn.py`, `doeff/effects/future.py`
- Handlers: `doeff/cesk/handlers/task.py`
- Runtime: `doeff/cesk/runtime/async_.py`
- Tests: `tests/cesk/test_async_runtime.py`
