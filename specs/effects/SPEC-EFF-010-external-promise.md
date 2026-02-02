# SPEC-EFF-010: ExternalPromise for External World Integration

## Status: Draft

## Problem Statement

doeff programs need to receive results from the external world:
- Python asyncio coroutines
- Network requests
- Cross-process communication
- User input
- Hardware events

Currently, this is handled via `PythonAsyncSyntaxEscape` with complex batching logic in the scheduler. This approach has problems:

1. **Scheduler knows about asyncio** - violates separation of concerns
2. **Batching complexity** - `PENDING_IO_KEY`, `_SchedulerSuspendForIO`, multi-task escape coordination
3. **Not universal** - tied to Python async, can't easily extend to other external sources
4. **Mixed concerns** - scheduler manages both Tasks and asyncio coordination

## Design Goals

1. **Universal** - works with any external completion source (asyncio, threads, processes, network)
2. **Contained impurity** - ExternalPromise is explicitly impure, regular Promise stays pure
3. **Simple scheduler** - scheduler only manages Tasks and Promises, no asyncio knowledge
4. **Queue-based notification** - thread-safe, works across execution contexts

## Design

### ExternalPromise Type

```python
@dataclass
class ExternalPromise(Generic[T]):
    """Promise that can be completed from outside the CESK machine.

    IMPURE: This type is for bridging external world to doeff.
    Do NOT use inside pure doeff programs - use regular Promise instead.

    The completion methods (complete/fail) submit to a thread-safe queue
    that the scheduler checks during stepping.
    """
    _handle: Any                    # For scheduler waiter tracking
    _id: UUID                       # Universal identifier
    _completion_queue: Queue        # Reference to scheduler's queue

    @property
    def id(self) -> UUID:
        """Unique ID for this promise. Can be serialized for cross-process use."""
        return self._id

    @property
    def future(self) -> Future[T]:
        """The waitable side. Use `yield Wait(promise.future)` in doeff."""
        return Future(_handle=self._handle)

    def complete(self, value: T) -> None:
        """Complete the promise with a value. Called from external code."""
        self._completion_queue.put((self._id, value, None))

    def fail(self, error: BaseException) -> None:
        """Fail the promise with an error. Called from external code."""
        self._completion_queue.put((self._id, None, error))
```

### Scheduler External Completion Queue

The scheduler maintains a thread-safe queue for external completions:

```python
# In scheduler state (or handler context)
_external_completion_queue: queue.Queue[tuple[UUID, Any, BaseException | None]]
_external_promise_registry: dict[UUID, Any]  # Maps promise ID to handle
```

During stepping, the scheduler checks the queue:

```python
def _process_external_completions(store: dict) -> None:
    """Check queue and wake up waiters for completed external promises."""
    queue = store.get(EXTERNAL_COMPLETION_QUEUE_KEY)
    registry = store.get(EXTERNAL_PROMISE_REGISTRY_KEY, {})

    while not queue.empty():
        try:
            promise_id, value, error = queue.get_nowait()
            handle = registry.get(promise_id)
            if handle is not None:
                # Mark promise complete and wake waiters
                # (same mechanism as CompletePromise/FailPromise effects)
                _complete_promise_internal(store, handle, value, error)
        except queue.Empty:
            break
```

### CreateExternalPromise Effect

```python
@dataclass(frozen=True)
class CreateExternalPromiseEffect(EffectBase):
    """Create an ExternalPromise for external world integration."""
    pass

def CreateExternalPromise() -> Effect:
    """Create a promise that can be completed from outside doeff.

    Returns:
        ExternalPromise with complete()/fail() methods for external code.

    Example:
        @do
        def program():
            promise = yield CreateExternalPromise()

            # Pass promise to external code (e.g., asyncio)
            asyncio.create_task(async_work(promise))

            # Wait for external completion
            result = yield Wait(promise.future)
            return result
    """
    return create_effect_with_trace(CreateExternalPromiseEffect(), skip_frames=3)
```

### Handler Implementation

```python
def handle_create_external_promise(effect, ctx):
    """Handle CreateExternalPromiseEffect."""
    promise_id = uuid4()

    # Create handle (same as regular promise)
    handle_id, _ = create_promise_handle(ctx.store)

    # Register in external promise registry
    registry = ctx.store.get(EXTERNAL_PROMISE_REGISTRY_KEY, {})
    registry[promise_id] = handle_id
    ctx.store[EXTERNAL_PROMISE_REGISTRY_KEY] = registry

    # Get the completion queue
    completion_queue = ctx.store.get(EXTERNAL_COMPLETION_QUEUE_KEY)

    # Create ExternalPromise with queue reference
    external_promise = ExternalPromise(
        _handle=handle_id,
        _id=promise_id,
        _completion_queue=completion_queue,
    )

    return external_promise
```

## Usage Examples

### Example 1: Asyncio Integration

```python
@do
def fetch_url(url: str):
    """Fetch URL using asyncio, integrated via ExternalPromise."""
    promise = yield CreateExternalPromise()

    async def do_fetch():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    result = await response.text()
                    promise.complete(result)
        except Exception as e:
            promise.fail(e)

    # Fire and forget - asyncio runs independently
    asyncio.create_task(do_fetch())

    # Wait for completion
    result = yield Wait(promise.future)
    return result
```

### Example 2: Cross-Thread Communication

```python
@do
def compute_in_thread(data):
    """Offload computation to a thread."""
    promise = yield CreateExternalPromise()

    def worker():
        try:
            result = expensive_computation(data)
            promise.complete(result)
        except Exception as e:
            promise.fail(e)

    threading.Thread(target=worker).start()

    result = yield Wait(promise.future)
    return result
```

### Example 3: Network/IPC (via ID)

```python
@do
def request_from_service(request_data):
    """Send request to external service, wait for response."""
    promise = yield CreateExternalPromise()

    # Send request with promise ID (ID is serializable)
    send_to_service({
        "request": request_data,
        "callback_promise_id": str(promise.id),
    })

    # External service will call back with the promise ID
    # Our adapter receives it and calls promise.complete()/fail()

    result = yield Wait(promise.future)
    return result
```

## Integration with async_run

For `async_run`, asyncio coroutines can be wrapped automatically:

```python
# In async_run or a helper handler
async def wrap_coroutine_with_external_promise(coro, promise):
    """Wrap a coroutine to complete an ExternalPromise when done."""
    try:
        result = await coro
        promise.complete(result)
    except Exception as e:
        promise.fail(e)

# Usage in handler for Await effect:
def handle_await_effect(effect, ctx):
    promise = create_external_promise(ctx)

    # Schedule the coroutine (fire and forget)
    asyncio.create_task(
        wrap_coroutine_with_external_promise(effect.awaitable, promise)
    )

    # Return Wait on the promise's future
    return Wait(promise.future)
```

## Comparison: Promise vs ExternalPromise

| Aspect | Promise | ExternalPromise |
|--------|---------|-----------------|
| Purity | Pure | Impure |
| Completion | Via effects (CompletePromise/FailPromise) | Via methods (complete/fail) |
| Use case | doeff-to-doeff communication | External world → doeff |
| Thread safety | N/A (single-threaded CESK) | Thread-safe queue |
| Serializable | No | ID is serializable |

## Migration from Current Architecture

1. **Remove from scheduler**:
   - `_SchedulerSuspendForIO`
   - `_SchedulerAddPendingIO`, `_SchedulerGetPendingIO`, `_SchedulerRemovePendingIO`, `_SchedulerResumePendingIO`
   - `PENDING_IO_KEY`
   - `_AsyncEscapeIntercepted` handling for batching
   - `_build_multi_task_escape_from_pending`

2. **Add to scheduler**:
   - `EXTERNAL_COMPLETION_QUEUE_KEY`
   - `EXTERNAL_PROMISE_REGISTRY_KEY`
   - Queue checking in step loop

3. **Update async handlers**:
   - `python_async_syntax_escape_handler` uses ExternalPromise internally
   - Returns `Wait(promise.future)` instead of `PythonAsyncSyntaxEscape`

4. **Simplify async_run**:
   - No more batching/multi-task escape handling
   - Just step loop + queue checking

## Design Decisions

1. **Queue initialization**: Created idempotently in the CESK store.
   - On first access, check if exists, create if not
   - Standard lazy initialization pattern

2. **Queue checking frequency**: Every step, non-blocking.
   - Use `queue.empty()` check first (fast path)
   - Only call `queue.get_nowait()` if not empty
   - Never block on `queue.get()`

3. **Timeout/cancellation**: Out of scope.
   - Timeout is a Task-level effect, not ExternalPromise concern
   - Use existing `Cancel` effect or wrap with timeout at Task level

## References

- SPEC-CESK-003: Minimal Frame Architecture
- SPEC-CESK-EFFECT-BOUNDARIES: Python async escape boundaries
- Current Promise implementation: `doeff/effects/promise.py`
- Current scheduler: `doeff/cesk/handlers/task_scheduler_handler.py`
