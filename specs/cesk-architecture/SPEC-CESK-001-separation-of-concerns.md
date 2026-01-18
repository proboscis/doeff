# SPEC-CESK-001: Separation of Concerns in CESK Architecture

## Status: Draft

## Summary

This spec defines the separation of responsibilities between `step.py`, handlers, and runtimes in the doeff CESK architecture. The goal is to eliminate hardcoded control-flow from step.py and make all effects extensible through handlers.

## Problem Statement

The current implementation has inconsistent separation of concerns:

1. **step.py handles control-flow effects directly** - LocalEffect, GatherEffect, SafeEffect, ListenEffect, InterceptEffect, GraphCaptureEffect are all handled inside step.py by pushing frames onto K
2. **Handlers only receive non-control-flow effects** - Effects that reach handlers are already filtered
3. **Runtimes cannot override control-flow behavior** - AsyncRuntime cannot implement parallel Gather because step.py handles it sequentially first

This violates the design principle: "Handlers directly manipulate continuations, return TaskState"

## Design Principles

### 1. step.py: Pure State Transition

`step.py` should ONLY handle:
- **Program evaluation** - Converting `ProgramControl` to generator, advancing generators
- **Value/Error propagation through K** - Unwinding frames when Value or Error is produced
- **Effect extraction** - When a generator yields an effect, return `Suspended`

`step.py` should NOT:
- Interpret specific effect types
- Push frames for specific effects
- Make decisions based on effect semantics

### 2. Handlers: Effect Interpretation

Handlers receive ALL effects (no exceptions) and decide how to handle them:

```python
Handler = Callable[[Effect, TaskState, Store], FrameResult]

# FrameResult options:
# - ContinueValue(value, env, store, k)      # Resume with value
# - ContinueError(error, env, store, k)      # Resume with error  
# - ContinueProgram(program, env, store, k)  # Execute sub-program (can modify K)
```

Handlers can:
- Return immediate values (`ContinueValue`)
- Push frames by modifying K in the result (`ContinueProgram` with new frames)
- Delegate to runtime via special return types

### 3. Runtime: Scheduling and Coordination

Runtimes handle:
- Task scheduling (which task runs next)
- Async operations (Await, Delay, WaitUntil)
- Multi-task coordination (Gather parallelism, Spawn)
- Time simulation
- External I/O execution

Runtimes can:
- Override default handlers for runtime-specific behavior
- Intercept `Suspended` effects before dispatching to handlers
- Manage multiple concurrent tasks

## Target Architecture

```
Program Execution Flow:
┌─────────────────────────────────────────────────────────────┐
│                         Runtime                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                    Scheduler Loop                     │    │
│  │  1. Pick ready task                                   │    │
│  │  2. Call step(state)                                  │    │
│  │  3. Match result:                                     │    │
│  │     - Done/Failed → terminal                          │    │
│  │     - CESKState → continue stepping                   │    │
│  │     - Suspended → dispatch to handler OR intercept    │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                         step.py                              │
│  - Advance generator (next/send/throw)                       │
│  - Extract yielded Effect → return Suspended                 │
│  - Propagate Value/Error through K frames                    │
│  - NO effect-specific logic                                  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        Handlers                              │
│  - Receive ALL effects (control-flow and data effects)       │
│  - Return FrameResult (value, error, or sub-program)         │
│  - Can push frames by returning ContinueProgram with new K   │
│  - Pure functions, no side effects                           │
└─────────────────────────────────────────────────────────────┘
```

## Effect Classification (After Refactor)

| Effect | Handler Returns | Notes |
|--------|-----------------|-------|
| AskEffect | ContinueValue | Read from env |
| GetEffect | ContinueValue | Read from store |
| PutEffect | ContinueValue | Write to store |
| ModifyEffect | ContinueValue | Modify store |
| TellEffect | ContinueValue | Append to log |
| PureEffect | ContinueValue | Return value |
| LocalEffect | ContinueProgram + LocalFrame | Push frame, run sub-program |
| SafeEffect | ContinueProgram + SafeFrame | Push frame, run sub-program |
| ListenEffect | ContinueProgram + ListenFrame | Push frame, run sub-program |
| InterceptEffect | ContinueProgram + InterceptFrame | Push frame, run sub-program |
| GatherEffect | ContinueProgram + GatherFrame | Sequential (default handler) |
| GatherEffect | *Runtime intercepts* | Parallel (AsyncRuntime) |
| DelayEffect | ContinueValue (SyncRuntime) | Or runtime intercepts for async |
| AwaitEffect | *Runtime intercepts* | Async operation |
| IOEffect | ContinueValue | Execute and return |
| CacheGetEffect | ContinueValue | Read from cache store |

## Migration Plan

### Phase 1: Move Control-Flow Effects to Handlers

Remove from step.py:
- LocalEffect handling (lines ~74-81)
- InterceptEffect handling (lines ~83-89)
- WriterListenEffect handling (lines ~91-98)
- GatherEffect handling (lines ~100-110)
- ResultSafeEffect handling (lines ~112-118)
- GraphCaptureEffect handling (lines ~120-127)

Add to handlers:
- `handlers/control.py`: handle_local, handle_intercept, handle_listen, handle_safe
- `handlers/task.py`: handle_gather (sequential default)
- `handlers/graph.py`: handle_graph_capture

### Phase 2: Update step.py

Simplify step.py to:
1. Handle ProgramControl → start/advance generator
2. Handle Value + K → call frame.on_value() or propagate
3. Handle Error + K → call frame.on_error() or propagate
4. Handle EffectControl → return Suspended(effect)

Remove:
- `is_control_flow_effect()` checks
- Direct effect type matching
- Frame pushing logic

### Phase 3: Update Runtimes

Update runtimes to intercept effects they want to handle specially:
- AsyncRuntime: intercept GatherEffect for parallel execution
- AsyncRuntime: intercept DelayEffect, WaitUntilEffect, AwaitEffect
- SimulationRuntime: intercept time effects for simulation

### Phase 4: Update Classification

Remove or repurpose `classification.py`:
- `is_control_flow_effect()` no longer needed
- `is_pure_effect()` / `is_effectful()` may still be useful for optimization

## Success Criteria

1. step.py has no effect-type-specific logic
2. All effects flow through handlers (can be intercepted by runtime first)
3. AsyncRuntime can implement parallel Gather
4. New control-flow effects can be added without modifying step.py
5. All existing tests pass

## Related Issues

- [#154](https://github.com/CyberAgentAILab/doeff/issues/154): Refactor step.py to remove control-flow handling
- [#155](https://github.com/CyberAgentAILab/doeff/issues/155): Move control-flow effects to handlers
- [#156](https://github.com/CyberAgentAILab/doeff/issues/156): AsyncRuntime parallel Gather
- [#157](https://github.com/CyberAgentAILab/doeff/issues/157): Fix async store snapshot bug

### Execution Order

```
Phase 1 (parallel):
  - #155: Create handlers for control-flow effects
  - #157: Fix async store snapshot bug (independent)

Phase 2 (after Phase 1):
  - #154: Refactor step.py (needs handlers from #155)

Phase 3 (after Phase 2):
  - #156: AsyncRuntime parallel Gather (needs GatherEffect to flow through)
```

## References

- Original design: `doeff-vault/issues/ISSUE-CORE-456.md`
- Handler implementation: `doeff/cesk/handlers/`
- Current step.py: `doeff/cesk/step.py`
