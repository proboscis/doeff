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

## Current State vs Target

### Done

| Phase | PR | What |
|-------|----|------|
| 1 | #356 | Moved dispatch frames to segment fields (intermediate) |
| 2 | #354 | Shared handlers via caller chain |
| 3 | #353 | Scoped variables API (AllocVar/ReadVar/WriteVar) |

### Remaining

| Phase | What |
|-------|------|
| 4 | Rename Segment→Fiber, caller→parent, eliminate non-OCaml fields |
| 4 | Move variables from fibers to VarStore (separate heap) |
| 4 | Move mode/pending_python from fibers to VM registers |
| 4 | Replace Arc<Segment> snapshots with move semantics (Continuation owns FiberIds) |
| 4 | Remove handler_dispatch/dispatch_origin/dispatch_id from fibers |
| 4 | Semgrep enforcement of fiber immutability |
| 5 | Clean up: remove DispatchId, simplify Mode, remove dead infrastructure |

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
