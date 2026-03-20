# SPEC-VM-019: Pure Stack Machine and Scoped Variables (Rev 4)

**Status:** Draft
**Created:** 2026-03-18
**Revised:** 2026-03-20
**Motivation:** Handler duplication in Spawn (#342), dispatch complexity, RustStore violation, ScopeStore leaking into VM

**ADR:** [DEC-VM-012 Pure Stack Machine Dispatch](../../doeff-VAULT/Decisions/DEC-VM-012-pure-stack-machine-dispatch.md)

## Problem Statement

The doeff VM has accumulated accidental complexity in its dispatch mechanism. Comparing
with OCaml 5's effect handler runtime reveals that doeff's core mechanics are structurally
identical but obscured by layered concerns:

1. **Dispatch state on segments** — `handler_dispatch` and `dispatch_origin` fields on
   segments encode tracing, protocol enforcement, and error enrichment directly in the
   segment chain. These are observability concerns, not dispatch mechanics.

2. **Handler duplication in Spawn** — spawned tasks previously got cloned handler segments
   instead of sharing the parent's handlers via the caller chain. (Fixed in PR #354.)

3. **Handler-specific fields in RustStore** — `state`, `env`, `log` were handler
   implementation details leaking into the VM. (Fixed in PR #353 via scoped variables.)

4. **Caller chain mutation during dispatch** — `handle_dispatch_resume` mutates
   `seg.caller` for Python handlers, polluting shared handler chains. This is because
   dispatch state (return path) is tracked on segments instead of being implicit in the
   stack topology.

## Design Principle: Pure Stack Machine

### OCaml 5 Architecture (from PLDI 2021 paper)

OCaml 5 implements effect handlers with **fibers** — small heap-allocated stack chunks:

```
Fiber = stack chunk containing:
  - Stack frames (function calls)
  - Handler delimiter (handler closure + environment)
  - Link to parent fiber

Program stack = linked list of fibers

perform E:
  1. Walk fiber chain to find matching handler delimiter
  2. Detach fibers between perform site and handler → this IS continuation k
     (just pointer manipulation, no copying — one-shot)
  3. Run handler code on the handler's fiber

continue k v:
  1. Reattach k's fiber chain
  2. Deliver value v
  3. Execution resumes from where perform suspended
```

Key properties:
- **No dispatch state anywhere** — dispatch IS the stack topology change
- **Immutable stack items** — fibers are not mutated during dispatch; only the
  linked list structure changes (detach/reattach)
- **Shared handlers** — spawned fibers delegate effects up the fiber chain to the same
  handler instances. No cloning.
- **One-shot enforcement** — flag on continuation object, checked at `continue` time
- **Tracing** — separate DWARF-based debugging, not inline in dispatch

### doeff Mapping

```
OCaml 5                         doeff (target)
───────────────────             ────────────────────
fiber (stack chunk)         =   Segment
stack frame                 =   Frame
handler delimiter           =   SegmentKind::PromptBoundary
fiber chain (linked list)   =   caller chain
program stack               =   segment chain via caller
captured continuation       =   Continuation (Arc<Segment> snapshot)
perform                     =   yield Effect → VM dispatch
continue k v                =   VM processes Resume(k, v)
```

### The Core Insight: Dispatch IS Pointer Manipulation

In a pure stack machine, dispatch state is not stored anywhere — it IS the topology
of the linked list of segments. The only mutable thing during dispatch is:

1. **Which segment is current** (`current_segment`)
2. **How segments are linked** (caller pointers)

Everything else — which dispatch is active, the return path, error context — is
derivable from the stack topology at any point. Storing dispatch state on segments
(as `handler_dispatch`, `dispatch_origin`, `dispatch_id`) is the VM "managing" dispatch
instead of just being a stack machine.

```
Pure stack machine dispatch:

yield Effect:
  1. Walk caller chain from current segment
  2. For each PromptBoundary: check if handler matches effect
  3. When found: detach segments between here and handler → this IS k
  4. current_segment = handler's segment
  5. Invoke handler with (effect, k)
  Done. No state written to any segment.

Resume(k, v):
  1. Assert !k.consumed (one-shot check on Continuation object)
  2. Set k.consumed = true
  3. Reattach k's segment chain (set caller pointers)
  4. current_segment = k's innermost segment
  5. Deliver v
  Done. No state read from segments except caller pointers.
```

### What About Tracing / Error Enrichment?

These are **observability concerns** that must be separated from the dispatch path:

- **Tracing**: A separate trace observer records dispatch events (start, complete,
  error). The observer can maintain its own data structures keyed by dispatch ID.
  The dispatch path itself does not need to know about tracing.

- **Error enrichment**: When a throw occurs, the active handler chain can be
  assembled on-demand by walking the segment chain. The segment chain already
  contains all handler info (PromptBoundary). No need to store DispatchOrigin.

- **One-shot enforcement**: A `consumed: bool` flag on the `Continuation` object.
  Checked when the VM processes Resume(k, v). Not a dispatch concern — it's a
  continuation validity check.

## Current State vs Target

### What's been done

| Phase | Status | PR |
|-------|--------|----|
| Scoped variables (AllocVar/ReadVar/WriteVar/WriteVarNonlocal) | Done | #353 |
| Shared handlers via caller chain | Done | #354 |
| Move dispatch frames to segment fields | Done | #356 |

### What remains: eliminate dispatch state from segments

PR #356 moved `HandlerDispatch` and `DispatchOrigin` from Frame enum variants to
segment fields. This was an intermediate step — the frames are gone, but the segment
still carries dispatch state:

```
Current (after PR #356):
Segment {
    handler_dispatch: Option<HandlerDispatchState>,  // ← should not exist
    dispatch_origin: Option<DispatchOriginState>,    // ← should not exist
    dispatch_id: Option<DispatchId>,                 // ← should not exist
    ...
}
```

Target:
```
Segment {
    kind: SegmentKind,
    caller: Option<SegmentId>,
    scope_parent: Option<SegmentId>,
    frames: Vec<Frame>,
    mode: Mode,
    variables: HashMap<VarId, Value>,
    // NO dispatch state. Segments are immutable stack items.
}
```

The dispatch mechanism should be pure pointer manipulation on the segment chain.
Tracing/error enrichment should live in a separate observer, not on segments.

## Spawn and Shared Handlers (OCaml Model)

In OCaml 5, spawned fibers share parent handlers. Effects from child tasks delegate
up to the same handler instances:

```
Parent task:                    Spawned task:
┌──────────┐                    ┌──────────┐
│ body seg │                    │ task seg │
│ caller ──┼──┐                 │ caller ──┼──┐
└──────────┘  │                 └──────────┘  │
              ▼                               │
         ┌──────────┐                         │
         │Scheduler │ ◄───────────────────────┘
         │ caller ──┼──┐
         └──────────┘  │
                       ▼
         ┌──────────────┐
         │ StateHandler │  ← SAME instance serves both tasks
         └──────────────┘
```

`CreateContinuation(program)` creates a continuation whose body segment has
`caller = current segment` (the scheduler's handler segment). When the task
yields an effect, it walks up from task body segment via caller, hits the
scheduler, continues up to StateHandler — sharing the same handler instances.

No `GetHandlers`, no `clone_spawn_scope_chain`, no `scope_parent` needed for
handler lookup. Effects delegate up the caller chain naturally.

`scope_parent` is retained only for variable lookup (ReadVar walks scope_parent)
where lexical and dynamic scope diverge (e.g., spawned task needs yield site's
variables but scheduler's return path).

## Scoped Variables

Handler state lives in generic scoped variables on segments, not in handler-specific
VM fields:

```
AllocVar(initial_value) → VarId
    Allocates a variable in the current segment.
    Returns opaque VarId. Lifetime tied to segment.

ReadVar(VarId) → Value
    Reads variable. Walks scope_parent chain if not in current segment.

WriteVar(VarId, Value) → ()
    Writes to current segment (shadow semantics).

WriteVarNonlocal(VarId, Value) → ()
    Writes to the segment where VarId was allocated (nonlocal/mutate semantics).
```

Handler factories allocate variables once via `AllocVar`. Since spawned tasks share
the handler via caller chain, `ReadVar` from a spawned task walks up to the
handler's segment and reads the same variable. `Put` from any task writes to the
same variable via `WriteVarNonlocal`.

## Migration Plan

### Phase 1: Remove Special Dispatch Frames ✅ (PR #356)

- Moved HandlerDispatch and DispatchOrigin from Frame enum to segment fields
- Added semgrep rules to prevent reintroduction of frame variants
- All tests pass

**Note:** This was an intermediate step. Dispatch state still lives on segments
as `handler_dispatch`, `dispatch_origin`, `dispatch_id` fields.

### Phase 2: Shared Handlers via Caller Chain ✅ (PR #354)

- Spawned tasks share parent handlers via caller chain
- Fixed caller chain pollution in `handle_dispatch_resume`
- Removed `clone_spawn_scope_chain`
- All tasks share same handler instances

### Phase 3: Scoped Variables ✅ (PR #353)

- Added AllocVar/ReadVar/WriteVar/WriteVarNonlocal DoCtrl
- Removed RustStore.state/env/log
- Removed ScopeStore from Segment

### Phase 4: Pure Stack Machine Dispatch (TODO)

Eliminate all dispatch state from segments. Make dispatch pure pointer manipulation.

1. **Remove `handler_dispatch` from Segment** — the handler generator holds the
   effect and k as parameters. The return path is the caller chain. One-shot
   enforcement is a flag on Continuation. Nothing needs to be stored on the segment.

2. **Remove `dispatch_origin` from Segment** — error enrichment assembled on-demand
   from segment chain at throw time. The segment chain already contains all handler
   info via PromptBoundary.

3. **Remove `dispatch_id` from Segment** — tracing moves to a separate observer
   that maintains its own dispatch correlation state.

4. **Eliminate Python handler caller mutation** — with no dispatch state on segments,
   `handle_dispatch_resume` no longer needs to relink `seg.caller`. The return path
   is implicit in the stack topology. This fixes the Python handler hack from PR #354.

5. **Simplify dispatch path** — the dispatch becomes:
   - Walk caller chain → find handler → detach k → set current → invoke handler
   - Resume: assert one-shot → reattach k → deliver value
   - No state reads or writes on segments during dispatch

### Phase 5: Clean Up

- Remove accumulated dispatch infrastructure (DispatchId type, dispatch modes)
- Simplify Mode enum
- Address TODO items from PR #354 (scope cleanup perf, dispatch map growth)
- Evaluate whether `scope_parent` can be unified with `caller` for simpler cases

## Open Questions

### Handler Re-entrancy

When handler A is handling E1 and resumes k, and k yields E2 also handled by A:
each dispatch creates a new handler program invocation. The previous invocation's
state is captured as part of k. Need to verify this works cleanly with pure
stack machine dispatch.

### Performance of Caller Chain Walking

ReadVar walks the scope_parent chain — O(depth). For hot paths, may need caching.
OCaml's fiber chain walk is also O(depth) but with hardware-friendly memory layout.

### Trace Observer Architecture

Moving tracing out of dispatch requires designing the observer interface:
- How does the observer correlate dispatch start/complete events?
- How does it provide error context on demand?
- How does it handle concurrent dispatches (scheduler)?

This is a design question for Phase 4.
