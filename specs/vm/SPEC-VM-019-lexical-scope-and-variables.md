# SPEC-VM-019: Pure Stack Machine (Rev 5)

**Status:** Draft
**Created:** 2026-03-18
**Revised:** 2026-03-20
**Motivation:** Align doeff VM architecture with OCaml 5 effect handler runtime

**ADR:** [DEC-VM-012 Pure Stack Machine Dispatch](../../doeff-VAULT/Decisions/DEC-VM-012-pure-stack-machine-dispatch.md)

## Design Principle: Match OCaml 5

The doeff VM is the equivalent of the OCaml 5 runtime. The architecture must match
OCaml 5 as closely as possible — same data structures, same operations, same invariants.

```
OCaml 5 runtime                      doeff VM
──────────────────                   ──────────────────
Fiber chain (the stack)          =   Fiber chain (the stack)
Heap (ref cells, closures)       =   VarStore (the heap)
Registers (IP, exception state)  =   Registers (current_fiber, mode, pending)
perform / continue               =   yield Effect / Resume(k, v)
```

## OCaml 5 Architecture (from PLDI 2021 paper)

### Fiber

A fiber is a minimal stack chunk:

```
Fiber {
    frames: [stack frames]           // function calls
    handler: Option<HandlerDelimiter> // handler closure + effect types
    parent: Option<FiberId>          // link to parent fiber
}
```

That's it. No mode, no variables, no dispatch state, no scope pointer.

### Fiber Chain (the Stack)

The program stack is a linked list of fibers via `parent` pointers:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Fiber C     │     │ Fiber B     │     │ Fiber A     │
│ frames: [.] │     │ frames: [.] │     │ frames: [.] │
│ handler: -  │     │ handler: H2 │     │ handler: H1 │
│ parent ─────┼────►│ parent ─────┼────►│ parent: nil │
└─────────────┘     └─────────────┘     └─────────────┘
      ↑
  current_fiber
```

### Heap (Ref Cells)

Mutable state lives on the heap, not on fibers:

```ocaml
let handler body =
  let state = ref 0 in              (* allocate ref cell on heap *)
  match body () with
  | result -> result
  | effect Get, k -> continue k !state        (* read from heap *)
  | effect (Put v), k -> state := v; continue k ()  (* write to heap *)
```

The handler closure captures a pointer to the heap ref. Multiple fibers that
share the handler (via parent chain) access the same heap ref. The heap is
managed by the VM (garbage collected).

### Registers

The VM has a small number of mutable registers — not on fibers:

- **current_fiber** — which fiber is executing
- **exception state** — whether propagating an exception (deliver value vs throw)

### perform / continue (Dispatch)

Dispatch is pointer manipulation on the fiber chain:

```
perform E (handled by H2 on Fiber B):

  BEFORE:  C ──► B ──► A           AFTER:  B ──► A       k = [C]
                 ↑H2                        ↑H2 (current)    (moved)

  1. Walk parent chain: C → B, find H2 on B
  2. Detach C → C IS k (moved out of chain, not copied)
  3. current_fiber = B
  4. Invoke H2 with (effect, k)

continue k v:

  BEFORE:  B ──► A    k = [C]      AFTER:  C ──► B ──► A
           ↑ (current)                      ↑ (current)

  1. Reattach: C.parent = B (moved back into chain)
  2. current_fiber = C
  3. Deliver v
  4. k is consumed (one-shot — C was moved out)
```

Key properties:
- **Fibers are moved, not copied** — a fiber is in the chain OR in a continuation, never both
- **No mutation of non-current fibers** — only topology changes (parent pointers on detach/reattach)
- **No dispatch state** — dispatch IS the topology change
- **Traceability from stack** — walk the chain to derive context; no accumulated state

## doeff VM Architecture (Target)

### Fiber

Renamed from `Segment`. Minimal — matches OCaml 5 fiber:

```rust
struct Fiber {
    frames: Vec<Frame>,                       // stack frames
    handler: Option<HandlerDelimiter>,        // handler closure + effect types
    parent: Option<FiberId>,                  // link to parent fiber
}
```

Nothing else. No `mode`, no `variables`, no `scope_parent`, no `dispatch_id`,
no `handler_dispatch`, no `dispatch_origin`, no `pending_python`, no `kind`.

Renamed fields:
- `Segment` → `Fiber`
- `SegmentId` → `FiberId`
- `caller` → `parent`
- `SegmentKind::PromptBoundary` → `HandlerDelimiter`
- `current_segment` → `current_fiber`

### VarStore (the Heap)

Renamed from scoped variables on segments. Matches OCaml 5's heap for ref cells:

```rust
struct VarStore {
    cells: HashMap<VarId, Value>,     // heap-allocated ref cells
}
```

Variables live here, not on fibers. Handler closures (Python generators / Rust
IRStream programs) hold VarIds that point into this store.

```
AllocVar(initial)     → VarId       // like OCaml: ref initial
ReadVar(VarId)        → Value       // like OCaml: !ref
WriteVar(VarId, v)    → ()          // like OCaml: ref := v
```

VarStore is managed by the VM. Variables are garbage collected when no handler
holds a reference to their VarId (or when the run session ends).

### Registers

VM-level mutable state — not on fibers:

```rust
struct VMRegisters {
    current_fiber: Option<FiberId>,   // which fiber is executing
    mode: Mode,                       // Deliver / Throw / Return
    pending_python: Option<...>,      // GIL boundary state
}
```

These are the doeff equivalent of OCaml's instruction pointer and exception state.
They describe what the VM is doing with the current fiber, not a property of any fiber.

### Continuation

Owns moved fiber IDs — not Arc snapshots:

```rust
struct Continuation {
    fibers: Vec<FiberId>,            // moved out of the chain
    consumed: bool,                   // one-shot enforcement
}
```

Capture = detach fibers from chain, give ownership to Continuation.
Resume = reattach fibers to chain, mark Continuation as consumed.
Drop = free owned fibers from arena (if not resumed).

No `Arc<Segment>` cloning. A fiber is in the chain or in a Continuation, never both.

### Full VM Structure

```rust
struct VM {
    // The stack (fiber chain)
    arena: FiberArena,                // all fibers live here
    current_fiber: Option<FiberId>,   // top of stack

    // The heap (ref cells)
    var_store: VarStore,              // mutable variable storage

    // Registers
    mode: Mode,                       // Deliver / Throw / Return
    pending_python: Option<...>,      // GIL boundary
}
```

This maps 1:1 to OCaml 5:

```
OCaml 5 runtime          doeff VM
─────────────────         ─────────────────
fiber pool / GC       =   FiberArena
current fiber         =   current_fiber
heap                  =   VarStore
IP / exception state  =   mode / pending_python
```

## Dispatch (perform / continue)

Exactly OCaml 5's mechanics:

```
yield Effect (handled by H on Fiber B):

  1. Walk parent chain from current_fiber
  2. Find HandlerDelimiter on B that matches effect
  3. Detach fibers between current and B → Continuation k (moved)
  4. current_fiber = B
  5. Push handler Program frame onto B (with immutable effect_repr)
  6. Invoke handler with (effect, k)

  Only topology changes. No fiber fields mutated.

Resume(k, v):

  1. Assert !k.consumed
  2. k.consumed = true
  3. Reattach k's fibers: innermost.parent = current_fiber
  4. current_fiber = k's innermost fiber
  5. mode = Deliver(v)

  Only topology changes + register update. No fiber fields mutated.
```

## Shared Handlers (Spawn)

Same as OCaml 5 — spawned tasks share parent handlers via parent chain:

```
Task 1:   C1 ──► Scheduler ──► StateHandler
Task 2:   C2 ──► Scheduler ──┘
                                ↑
                  Always in the chain, never detached
                  Both tasks walk through the same handler instances
                  StateHandler's state is a VarId pointing into VarStore
```

When Task 1 performs: C1 is detached (moved to k). Scheduler and StateHandler
stay in the chain. Task 2's C2 still has parent → Scheduler. No mutation of
shared fibers.

## Traceability

The stack IS the trace:

1. Walk fiber chain
2. Fibers with `handler: Some(...)` and active Program frames → active dispatches
3. Program frame `effect_repr` (immutable) → which effect triggered it
4. Assemble traceback on demand

No `dispatch_origin`, no `dispatch_id`, no accumulated maps.

## Semgrep Enforcement

```yaml
# Fiber immutability
- ban: mutation of fiber.parent outside creation and detach/reattach
- ban: mutation of any fiber field via arena.get_mut(id) where id != current_fiber
  (except var_store operations which go through VarStore, not fibers)
- ban: Arc<Segment> / Arc<Fiber> — move semantics only
- ban: handler_dispatch / dispatch_origin / dispatch_id on Fiber
- ban: variables / scope_parent / mode / pending_python on Fiber

# Allowlist
- allow: current_fiber_mut() for pushing/popping frames during execution
- allow: VarStore mutations (AllocVar/ReadVar/WriteVar)
- allow: parent pointer changes during detach/reattach only
```

## Current State vs Target (Audit 2026-03-21)

### What's been done (surface)

| PR | What |
|----|------|
| #353 | Scoped variables API (AllocVar/ReadVar/WriteVar) |
| #354 | Shared handlers via caller chain |
| #356 | Dispatch frames → segment fields (intermediate) |
| #358 | Dispatch state off segments → DispatchObserver side-table |
| #359 | Segment→Fiber rename + memory leak fix |
| #360 | scope_bindings → VarStore module |

### Honest architectural gap

Despite 6 PRs, the VM architecture is fundamentally different from OCaml 5:

**Fiber has 21 fields. OCaml 5 fiber has 3.**

```
Current Fiber (21 fields):           Target Fiber (3 fields):
  frames ✅                            frames
  kind ✅                              handler
  caller ✅ (renamed parent)           parent
  mode ❌ VM register
  pending_python ❌ VM register
  variables ❌ VarStore heap
  named_bindings ❌ VarStore heap
  state_store ❌ VarStore heap
  writer_log ❌ VarStore heap
  scope_id ❌ remove
  scope_parent ❌ remove
  persistent_epoch ❌ remove
  marker ❌ fold into handler
  handler_dispatch ❌ remove
  dispatch_origin ❌ remove
  dispatch_id ❌ remove
  pending_error_context ❌ remove
  throw_parent ❌ remove
  interceptor_eval_depth ❌ remove
  interceptor_skip_stack ❌ remove
```

**VM has 22 fields. Target has ~5.**
13 fields are dispatch side-tables (dispatch_effects, dispatch_error_contexts,
continuation_registry, installed_handlers, etc.) that should not exist.

**Continuation uses Arc<Segment> deep-clone. OCaml 5 uses move semantics.**
- Capture = deep clone segment into Arc snapshot (creates copy)
- Resume = deep clone Arc snapshot into new arena segment (creates another copy)
- A fiber exists simultaneously in the chain AND in a continuation (violates invariant)
- OCaml: capture = detach (move pointer), resume = reattach (move pointer), zero copies

**Dispatch is not pointer manipulation.**
`start_dispatch` creates new segments, deep-clones, writes to 3+ HashMaps, calls
Python for type filters. `activate_continuation` materializes snapshots into new
segments. Neither is "walk, detach, reattach."

**mode is per-segment, not a VM register.**
Each segment carries its own `mode: Mode`. The VM reads `self.current_seg().mode`
instead of `self.mode`. This means the VM is not a register machine — every segment
is a mini-VM.

**Variables are dual-written.**
AllocVar writes to both `segment.variables` AND `vm.scope_variables`. The segment
copy exists for continuation snapshots (Arc clone captures it). The VM copy exists
for scope resolution. This dual-write is a symptom of Arc snapshots — with move
semantics, variables would live only in VarStore.

### Root cause: Arc<Segment> snapshots

Most of the architectural complexity traces back to **continuation capture via
Arc<Segment> deep clone**. Because capture copies segment data:

1. Variables must be dual-written (segment copy for snapshot, VM copy for resolution)
2. state_store/writer_log live on segments (for snapshot capture)
3. persistent_epoch exists to reconcile stale snapshots with live state
4. retired_scope_* maps exist to preserve state after segments are freed
5. dispatch_origin/handler_dispatch live on segments (copied into snapshot)
6. refresh_persistent_segment_state walks continuation chains updating state

**Fix Arc→move and most other problems dissolve.** With move semantics:
- Variables live only in VarStore (no dual-write, no reconciliation)
- State/logs live only in VarStore
- No persistent_epoch, no retired_scope_*, no refresh_persistent_segment_state
- No dispatch state on fibers (dispatch is topology change)
- Fiber becomes frames + handler + parent (3 fields)

### Migration order

The root cause analysis dictates the migration order:

1. **Move semantics first** — replace Arc<Segment> with detach/reattach. This is
   the keystone change that enables all subsequent simplification.
2. **Consolidate to VarStore** — once snapshots don't copy variables, remove them
   from fibers and use VarStore exclusively.
3. **Mode as register** — once fibers don't carry per-segment mode, move to VM.
4. **Strip remaining fields** — remove everything else from Fiber until only
   frames + handler + parent remain.
5. **Eliminate dispatch side-tables** — with move semantics, dispatch is topology
   change. Remove DispatchObserver, dispatch_effects, etc.

## Open Questions

### Fiber Arena Lifecycle with Move Semantics

When a Continuation is dropped without being resumed, its owned fibers must be freed.
Implement `Drop` for Continuation. This replaces Arc refcount-based cleanup.

### Multi-Fiber Continuations

When multiple fibers are detached (e.g., body C and intermediate B between current
and handler A), the Continuation owns [C, B]. On resume, the whole chain reattaches.

### VarStore Garbage Collection

Variables in VarStore need cleanup when handlers are done. Options:
- Reference counting (handler holds VarId, decrement on handler drop)
- Scope-based (tied to handler fiber lifetime — free vars when handler fiber freed)
- Run-session-based (clear all on session end, like current approach)

### InterceptorBoundary

OCaml 5 doesn't have interceptors. This is a doeff concept for middleware-like
effect transformation. Need to decide: does it become a variant of HandlerDelimiter,
or a separate mechanism? It should not add fields to Fiber.

### Handler Re-entrancy

When handler A handles E1, resumes k, and k yields E2 also reaching A: A's fiber
stays in the chain (never detached). A new Program frame is pushed. The old frame's
state is in k's detached chain. Verify this with move semantics.
