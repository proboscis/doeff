# VM-015 Spawn Interception Notes

## Scheduler simplification decision

Decision: defer scheduler simplification in VM-015.

### What was evaluated

- Removing the scheduler's `GetHandlers -> CreateContinuation` spawn path.
- Replacing that path with a new primitive such as
  `CreateContinuationInDispatchScope`.

### Why deferred

- VM-015 is focused on enabling Spawn interception through non-terminal
  `Delegate` at the Python handler layer.
- The scheduler simplification requires a separate VM control-primitive change
  (new dispatch-scope continuation creation path) and broader validation than
  this issue scope.
- Current behavior remains correct for Spawn interception and nested Spawn
  propagation with the existing scheduler path.

### Follow-up

- Keep the existing scheduler spawn flow in this change.
- Track `CreateContinuationInDispatchScope` and scheduler simplification as a
  dedicated follow-up issue.
