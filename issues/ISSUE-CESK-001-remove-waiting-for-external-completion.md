# ISSUE-CESK-001: Remove WaitingForExternalCompletion from StepResult

## Type: Refactor

## Priority: High

## Summary

Remove `WaitingForExternalCompletion` from `StepResult`. The task scheduler handler should own blocking behavior for external completion instead of delegating to the run loop.

## Background

`WaitingForExternalCompletion` is an architectural mistake that:
1. Leaks scheduler internals (queue) to run loop
2. Has run loop do blocking that should be handler's responsibility
3. Violates separation of concerns

See: `specs/cesk-architecture/SPEC-CESK-004-handler-owned-blocking.md`

## Current Behavior

```
task_scheduler_handler → WaitingForExternalCompletion → run.py polls queue
```

## Desired Behavior

```
task_scheduler_handler → blocks internally on queue → resumes when complete
```

## Tasks

### 1. Add _BlockForExternalCompletion internal effect
- [ ] Create `_BlockForExternalCompletion` effect in `scheduler_internal.py`
- [ ] Handle in `scheduler_state_handler` - block on `completion_queue.get()`

### 2. Update task_scheduler_handler
- [ ] Replace `return WaitingForExternalCompletion(...)` with blocking Program
- [ ] Ensure blocking only happens when no runnable tasks exist

### 3. Remove WaitingForExternalCompletion
- [ ] Delete from `doeff/cesk/result.py`
- [ ] Remove from `StepResult` type alias
- [ ] Remove handling from `doeff/cesk/run.py` (sync_run and async_run)
- [ ] Remove pass-through from `doeff/cesk/handler_frame.py`

### 4. Testing
- [ ] Verify all existing tests pass
- [ ] Add test for blocking behavior when waiting on external

## Files to Modify

- `doeff/cesk/result.py` - Remove WaitingForExternalCompletion
- `doeff/cesk/run.py` - Remove queue polling
- `doeff/cesk/handler_frame.py` - Remove pass-through
- `doeff/cesk/handlers/task_scheduler_handler.py` - Block internally
- `doeff/cesk/handlers/scheduler_state_handler.py` - Handle blocking effect
- `doeff/effects/scheduler_internal.py` - Add blocking effect

## Acceptance Criteria

- [ ] `WaitingForExternalCompletion` does not exist in codebase
- [ ] `run.py` has no `completion_queue` references
- [ ] All 685+ tests pass
- [ ] No user-facing behavior change

## Related

- Spec: SPEC-CESK-004-handler-owned-blocking.md
