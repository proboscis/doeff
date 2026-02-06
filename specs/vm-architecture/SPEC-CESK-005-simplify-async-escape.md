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
│   yield PythonAsyncSyntaxEscape(    │
│       action=lambda: asyncio.create_task(fire_task())
│   )                                 │
│   # Handler yields escape (returns value, e.g., Task object)
│   # step() wraps action to return CESKState
│   # async_run awaits and gets new state directly
│   # Then handler continues:         │
│   result = yield Wait(promise.future)
│   return result                     │
└─────────────────────────────────────┘
           │
           ▼
       step():
           # Sees escape effect, wraps action to return CESKState
           return PythonAsyncSyntaxEscape(
               action=lambda: wrap_with_state(original_action)
           )
           │
           ▼
       async_run:
           result = step(...)
           if isinstance(result, PythonAsyncSyntaxEscape):
               state = await result.action()  # Returns CESKState directly
               continue
           await asyncio.sleep(0)
```

**Key design**:
- Handler's action returns a **value**
- step() wraps it to return **CESKState** (capturing E, S, K)
- async_run just awaits and uses the state directly (no state construction logic)

### Simplified PythonAsyncSyntaxEscape

```python
@dataclass(frozen=True)
class PythonAsyncSyntaxEscape:
    """Minimal escape: run an async action in async_run's context.

    This exists ONLY because asyncio.create_task() requires a running
    event loop. The handler runs during step(), which is inside async_run,
    but we need to ensure asyncio context explicitly.

    The action returns a CESKState to resume with. This is constructed by
    step() which wraps the handler's value-returning action with state context.

    PRODUCERS: python_async_syntax_escape_handler, async_external_wait_handler
    """
    action: Callable[[], Awaitable[CESKState]]
```

That's it. No resume callbacks, no store fields, no multi-task handling.

**Key insight**: Handlers create actions that return **values**. step() wraps these
to return **CESKState**, capturing the current E, S, K. async_run just awaits and
uses the returned state directly.

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
            # Action returns CESKState directly (step() wraps handler's action)
            state = await result.action()
            continue

        if isinstance(result, Done):
            return result.value

        if isinstance(result, Failed):
            raise result.exception

        await asyncio.sleep(0)  # Yield to let asyncio tasks progress
        state = result  # CESKState
```

async_run is now minimal - it just awaits the action and uses the returned state.
The complexity of constructing CESKState is handled by step().

### step() Changes

When step() encounters a PythonAsyncSyntaxEscape effect, it wraps the action
to return CESKState:

```python
# In step.py
if isinstance(effect, PythonAsyncSyntaxEscape):
    original_action = effect.action

    async def wrapped_action():
        value = await original_action()
        return CESKState(C=Value(value), E=state.E, S=state.S, K=state.K)

    return PythonAsyncSyntaxEscape(action=wrapped_action)
```

This keeps state construction logic in step() where it belongs, and makes
async_run trivially simple.

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
   - Action signature: `Callable[[], Awaitable[CESKState]]`
   - Remove `python_async_escape` factory
   - Remove `multi_task_async_escape` factory

2. **doeff/cesk/step.py**
   - Wrap PythonAsyncSyntaxEscape action to return CESKState
   - Capture current E, S, K in wrapped action

3. **doeff/cesk/handlers/python_async_syntax_escape_handler.py**
   - Convert to `@do` function
   - Use `CreateExternalPromise` + `Wait` pattern
   - Yield simplified escape for `asyncio.create_task`
   - Action returns value (step() wraps to return CESKState)

4. **doeff/cesk/handlers/task_scheduler_handler.py**
   - Remove `_AsyncEscapeIntercepted` handling
   - Remove `_build_multi_task_escape_from_pending`
   - Remove all `PythonAsyncSyntaxEscape` imports/usage
   - Yield `WaitForExternalCompletion` when queue empty (per SPEC-CESK-004)

5. **doeff/cesk/run.py** (async_run)
   - Simplify escape handling to just `state = await result.action()`

6. **doeff/effects/scheduler_internal.py**
   - Remove `_AsyncEscapeIntercepted` effect
   - Add `WaitForExternalCompletion` effect

7. **NEW: doeff/cesk/handlers/sync_external_wait_handler.py**
   - Handle `WaitForExternalCompletion` with blocking `queue.get()`

8. **NEW: doeff/cesk/handlers/async_external_wait_handler.py**
   - Handle `WaitForExternalCompletion` with `run_in_executor` escape

### Backwards Compatibility

This is a breaking change for:
- Any code that constructs `PythonAsyncSyntaxEscape` directly (should be none outside core)
- Any code that accesses escape fields like `resume`, `awaitable`, etc.

The public API (`yield Await(coro)`) remains unchanged.

## Open Questions

1. ~~Should `PythonAsyncSyntaxEscape` be renamed to `AsyncAction` for clarity?~~ **Resolved: No.** The verbose name is intentional - it signals this is a special escape mechanism for Python's async syntax, not a general-purpose action.
2. ~~Should the escape yield mechanism be different (effect vs direct return)?~~ **Resolved: No.** Keep the current yield-based mechanism.

## References

- SPEC-CESK-004-handler-owned-blocking.md
- ExternalPromise implementation: doeff/effects/external_promise.py
