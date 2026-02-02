# SPEC-CESK-004: Handler-Owned Blocking for External Completion

## Status: Draft

## Summary

Remove `WaitingForExternalCompletion` from `StepResult`. The task scheduler handler should own its blocking behavior when waiting for external completion, rather than delegating to the run loop.

## Problem Statement

Currently, `WaitingForExternalCompletion` is a `StepResult` type that:

1. Is returned by `task_scheduler_handler` when no tasks are runnable but external promises are pending
2. Is checked in `run.py` which then polls the external completion queue
3. Leaks scheduler implementation details (queue management) to the run loop

```
Current (wrong):
  task_scheduler_handler
    └── returns WaitingForExternalCompletion(state=...)

  run.py
    └── sees WaitingForExternalCompletion
    └── polls completion_queue.get()
    └── processes completion
    └── resumes stepping
```

This violates separation of concerns:
- The handler knows about its queue but delegates blocking to run loop
- The run loop knows about scheduler internals (queue, completion processing)
- Two places manage what should be one concern

## Design Principles

### 1. doeff is Cooperative Scheduling

doeff has its own cooperative scheduling world. It does NOT "support" asyncio - `async_run` and `PythonAsyncSyntaxEscape` are hacks/workarounds for users who want `async def` syntax.

### 2. PythonAsyncSyntaxEscape is Exclusive

`PythonAsyncSyntaxEscape` is produced by ONE handler only: `python_async_syntax_escape_handler`. It converts `Await` effect to `PythonAsyncSyntaxEscape` for use with `async_run`. No other handler should produce this.

### 3. Handler Programs Block

When a handler returns a Program, stepping that Program should block (not proceed) until external promises resolve. The handler owns this blocking:

```python
@do
def wait_for_completion():
    # Blocking I/O inside generator - next() won't return until queue has item
    item = completion_queue.get()  # blocks here!
    # process item
    return result
```

When CESK steps this:
1. Calls `next(gen)`
2. Generator blocks on `queue.get()`
3. `next()` doesn't return until queue has item
4. CESK stepping is blocked

### 4. Blocking is Last Resort

The handler should only block when there is NO other work in the doeff world:

```
task_scheduler_handler scheduling loop:
  1. Runnable tasks exist? → run one (yield ResumeK)
  2. No runnable tasks, tasks waiting on external?
     → block on queue.get()  # only when nothing else to do
     → process completion, wake waiter
     → goto 1
  3. No tasks at all? → done or deadlock
```

## Target Architecture

```
Target (correct):
  run.py
    └── only steps, no queue knowledge
    └── only knows: Done | Failed | CESKState | PythonAsyncSyntaxEscape

  task_scheduler_handler
    └── owns completion queue
    └── exhausts all runnable work
    └── blocks internally on queue.get() when stuck
    └── resumes when external completes
```

### StepResult After Refactor

```python
# Before
StepResult = CESKState | Done | Failed | PythonAsyncSyntaxEscape | WaitingForExternalCompletion

# After
StepResult = CESKState | Done | Failed | PythonAsyncSyntaxEscape
```

## Implementation

### 1. Remove WaitingForExternalCompletion

- Delete from `doeff/cesk/result.py`
- Remove from `StepResult` type alias
- Remove handling in `doeff/cesk/run.py` (both sync and async)
- Remove pass-through in `doeff/cesk/handler_frame.py`

### 2. Update task_scheduler_handler

When `_SchedulerDequeueTask` returns no runnable task but external promises exist:

```python
# Current (wrong):
if next_task is None:
    pending_io = ctx.store.get(PENDING_IO_KEY, {})
    if pending_io:
        # ...
        return WaitingForExternalCompletion(state=...)

# Target (correct):
if next_task is None:
    pending_io = ctx.store.get(PENDING_IO_KEY, {})
    if pending_io:
        # Block on queue until completion arrives
        @do
        def wait_and_resume():
            completion = yield _BlockForExternalCompletion()
            # Process completion, wake waiter, continue scheduling
            ...
        return wait_and_resume()
```

### 3. Add Internal Blocking Effect

Create `_BlockForExternalCompletion` effect (internal to scheduler):
- Handled by `scheduler_state_handler`
- Blocks on `completion_queue.get()`
- Returns completion item when available

## Success Criteria

1. `WaitingForExternalCompletion` removed from codebase
2. `run.py` has no queue-related code
3. `task_scheduler_handler` blocks internally when waiting for external
4. All existing tests pass
5. No behavior change from user perspective

## Migration Notes

This is an internal refactor. User-facing API and behavior should not change.

## References

- Current implementation: `doeff/cesk/handlers/task_scheduler_handler.py`
- Run loop: `doeff/cesk/run.py`
- Result types: `doeff/cesk/result.py`
