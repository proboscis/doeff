# SPEC-EFF-007: Waitable API (Promise/Future/Wait/Task)

## Summary

Defines the API design for Promise, Future, Wait, Waitable, and Task primitives.

## Type Hierarchy

```
Waitable (Protocol)
├── Task (Class) - from Spawn effect
└── Future (Class) - from Promise.future
```

## Definitions

### Waitable (Protocol)

```python
@runtime_checkable
class Waitable(Protocol[T_co]):
    @property
    def _handle(self) -> Any:
        ...
```

- Protocol defining what `Wait` and `Gather` effects accept
- Both `Task` and `Future` implement this protocol via `_handle` property

### Future (Class)

```python
@dataclass(frozen=True)
class Future(Generic[T]):
    _handle: Any
```

- Read-only handle for values that arrive in the future
- Implements `Waitable` protocol
- Returned by `Promise.future` property
- User waits on this to get the promised value

### Promise (Class)

```python
@dataclass
class Promise(Generic[T]):
    _promise_handle: Any
    
    @property
    def future(self) -> Future[T]:
        return Future(_handle=self._promise_handle)
```

- Writer-side handle for completing/failing a future value
- Used with `CompletePromise(promise, value)` effect
- Used with `FailPromise(promise, error)` effect
- Has `.future` property for read-only access

### Task (Class)

```python
@dataclass(frozen=True)
class Task(Generic[T]):
    backend: SpawnBackend
    _handle: Any
```

- Handle returned from `Spawn` effect
- Implements `Waitable` protocol via `_handle`
- Used for `task.cancel()` and `task.is_done()` operations
- Can be directly passed to `Wait` and `Gather`

### Wait (Effect)

```python
def Wait(waitable: Waitable[T]) -> Effect
```

- Accepts any `Waitable` (both `Task` and `Future`)
- Blocks current task until waitable completes
- Returns result value or propagates error

### Gather (Effect)

```python
def Gather(*waitables: Waitable[Any]) -> Effect
```

- Accepts multiple `Waitable` objects
- Waits for all to complete
- Returns results in input order

## Usage Examples

### Task (from Spawn)

```python
@do
def with_task():
    task = yield Spawn(background_work())
    result = yield Wait(task)  # Task implements Waitable
    return result
```

### Promise/Future

```python
@do
def with_promise():
    promise = yield CreatePromise()
    
    @do
    def completer():
        yield CompletePromise(promise, "done")
    
    yield Spawn(completer())
    result = yield Wait(promise.future)  # Future implements Waitable
    return result
```

### Gather with Mixed Waitables

```python
@do
def with_gather():
    task = yield Spawn(work1())
    promise = yield CreatePromise()
    # ... someone completes promise elsewhere
    results = yield Gather(task, promise.future)  # Both are Waitable
    return results
```

## Implementation Notes

1. `Waitable` is `@runtime_checkable` for isinstance checks in handlers
2. `Future` is a concrete frozen dataclass, not a Protocol
3. `Task._handle` makes Task match `Waitable` protocol structurally
4. `Promise._promise_handle` is internal; `.future` exposes read-only `Future`
5. Scheduler handler uses `_handle` to look up state in task/promise registry
6. Both Task and Future can be used interchangeably where Waitable is expected
