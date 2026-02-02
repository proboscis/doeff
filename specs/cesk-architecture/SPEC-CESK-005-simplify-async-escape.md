# SPEC-CESK-005: Simplify PythonAsyncSyntaxEscape

## Status: Draft

## Summary

Simplify `PythonAsyncSyntaxEscape` from a complex continuation-saving mechanism to a minimal "run this async action" signal. All task coordination moves to the scheduler via `ExternalPromise`.

## Problem

Current `PythonAsyncSyntaxEscape` is overly complex:

```python
@dataclass
class PythonAsyncSyntaxEscape:
    resume: Callable[[Any, Store], CESKState]      # Complex resume callback
    resume_error: Callable[[BaseException], CESKState]
    awaitable: Any | None = None
    awaitables: dict[Any, Any] | None = None       # Multi-task coordination
    store: Store | None = None
    _propagating: bool = False
    _is_final: bool = False
    _stored_k: Any = None                          # Saved continuation
    _stored_env: Any = None
    _stored_store: Any = None
```

This complexity exists because:
1. It saves the continuation (K) to resume after await
2. It handles multi-task coordination (multiple awaitables)
3. It has custom resume callbacks to reconstruct CESKState

Additionally, `task_scheduler_handler` violates the "sole producer" principle by constructing its own escapes for multi-task coordination.

## Solution

### New Architecture

```
Task A: yield Await(coro_a)
           │
           ▼
┌─────────────────────────────────────┐
│ python_async_syntax_escape_handler  │
│ (becomes @do function)              │
│                                     │
│   promise = yield CreateExternalPromise()
│                                     │
│   async def fire_task():            │
│       try:                          │
│           result = await coro_a     │
│           promise.complete(result)  │
│       except BaseException as e:    │
│           promise.fail(e)           │
│                                     │
│   return PythonAsyncSyntaxEscape(   │
│       action=lambda: asyncio.create_task(fire_task())
│   )                                 │
│   # Handler yields escape, async_run executes action
│   # Then handler continues:         │
│   result = yield Wait(promise.future)
│   return result                     │
└─────────────────────────────────────┘
           │
           ▼
       async_run:
           result = step(...)
           if isinstance(result, PythonAsyncSyntaxEscape):
               await result.action()  # Fire and forget
               continue               # Resume stepping immediately
           await asyncio.sleep(0)     # Yield to event loop
```

### Simplified PythonAsyncSyntaxEscape

```python
@dataclass(frozen=True)
class PythonAsyncSyntaxEscape:
    """Minimal escape: run an async action in async_run's context.

    This exists ONLY because asyncio.create_task() requires a running
    event loop. The handler runs during step(), which is inside async_run,
    but we need to ensure asyncio context explicitly.

    The action should be fire-and-forget (e.g., create_task with callback).
    async_run executes the action and immediately continues stepping.

    SOLE PRODUCER: python_async_syntax_escape_handler only.
    """
    action: Callable[[], Awaitable[None]]
```

That's it. No resume, no K, no store, no multi-task handling.

### Handler Changes

`python_async_syntax_escape_handler` becomes a `@do` function:

```python
@do
def python_async_syntax_escape_handler(effect: EffectBase, ctx: HandlerContext):
    if isinstance(effect, PythonAsyncioAwaitEffect):
        # Create external promise for scheduler coordination
        promise = yield CreateExternalPromise()

        # Wrap awaitable with promise completion
        async def fire_task():
            try:
                result = await effect.awaitable
                promise.complete(result)
            except BaseException as e:
                promise.fail(e)

        # Escape to async_run to create the task
        yield PythonAsyncSyntaxEscape(
            action=lambda: asyncio.create_task(fire_task())
        )

        # Wait on promise (scheduler handles coordination)
        result = yield Wait(promise.future)
        return result

    # Forward unhandled effects
    result = yield effect
    return result
```

### async_run Changes

```python
async def async_run(program, handlers):
    state = initial_state(program, handlers)

    while True:
        result = step(state)

        if isinstance(result, PythonAsyncSyntaxEscape):
            await result.action()  # Execute async action
            continue               # Resume stepping (no state change)

        if isinstance(result, Done):
            return result.value

        if isinstance(result, Failed):
            raise result.exception

        await asyncio.sleep(0)  # Yield to let asyncio tasks progress
        state = result  # CESKState
```

### task_scheduler_handler Changes

Remove ALL `PythonAsyncSyntaxEscape` handling:
- Remove `_AsyncEscapeIntercepted` handling
- Remove `_build_multi_task_escape_from_pending`
- Remove `multi_task_async_escape` calls
- Scheduler becomes transparent to escapes

The scheduler coordinates via its normal mechanisms:
- `CreateExternalPromise` / `Wait(promise.future)`
- Queue-based blocking when no tasks runnable

## Benefits

1. **Simplicity**: Escape is just "run this async action"
2. **Separation of Concerns**:
   - Escape handles asyncio context requirement
   - Scheduler handles all task coordination
3. **Single Producer**: Only `python_async_syntax_escape_handler` produces escapes
4. **No Continuation Saving**: Scheduler's Wait/Promise handles resumption

## Migration

### Files to Modify

1. **doeff/cesk/result.py**
   - Simplify `PythonAsyncSyntaxEscape` to single `action` field
   - Remove `python_async_escape` factory
   - Remove `multi_task_async_escape` factory

2. **doeff/cesk/handlers/python_async_syntax_escape_handler.py**
   - Convert to `@do` function
   - Use `CreateExternalPromise` + `Wait` pattern
   - Yield simplified escape for `asyncio.create_task`

3. **doeff/cesk/handlers/task_scheduler_handler.py**
   - Remove `_AsyncEscapeIntercepted` handling
   - Remove `_build_multi_task_escape_from_pending`
   - Remove all `PythonAsyncSyntaxEscape` imports/usage

4. **doeff/cesk/run.py** (async_run)
   - Simplify escape handling to just `await result.action()`

5. **doeff/effects/scheduler_internal.py**
   - Remove `_AsyncEscapeIntercepted` effect

### Backwards Compatibility

This is a breaking change for:
- Any code that constructs `PythonAsyncSyntaxEscape` directly (should be none outside core)
- Any code that accesses escape fields like `resume`, `awaitable`, etc.

The public API (`yield Await(coro)`) remains unchanged.

## Open Questions

1. Should `PythonAsyncSyntaxEscape` be renamed to `AsyncAction` for clarity?
2. Should the escape yield mechanism be different (effect vs direct return)?

## References

- SPEC-CESK-004-handler-owned-blocking.md
- ExternalPromise implementation: doeff/effects/external_promise.py
