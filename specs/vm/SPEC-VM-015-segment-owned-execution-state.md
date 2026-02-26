# SPEC-VM-015: Segment-Owned Execution State

## Status: Draft

## 1. Overview

This spec defines the target architecture for the doeff Rust VM's execution state management. Today, 6 execution-local fields live as globals on the `VM` struct (or its sub-structs). This spec mandates that all execution-local state be **owned by `Segment`** — the VM's execution context primitive — following established abstract machine models for algebraic effects.

The single remaining global is `current_segment: SegmentId` — a pointer to the active execution context. This mirrors the universal pattern across OCaml fibers, libmprompt gstacks, and Koka's context struct: **one global pointer, everything else on the context**.

### 1.1 Scope

This spec covers:
- The theoretical foundation for segment-owned state
- The target `Segment` and `VM` struct definitions
- State transition rules for dispatch, resume, transfer, and interceptor eval
- Invariants the implementation must uphold
- What this spec does NOT change (handler semantics, DoCtrl vocabulary, Python bridge)

### 1.2 Relationship to Existing Specs

- **SPEC-008 (Rust VM)**: This spec refines the internal state model. SPEC-008's stepping semantics, DoCtrl vocabulary, and handler dispatch protocol are unchanged. The step loop reads/writes segment-local state instead of VM-global state.
- **SPEC-VM-PROTOCOL**: Unaffected. The VM↔Python boundary sees no change — `StepEvent`, `PythonCall`, `Mode`, `DoCtrl` all retain their definitions.
- **VM-SCOPE-001 (parent issue)**: This spec is the design document that VM-SCOPE-001's Phase 2+3 work will implement.

### 1.3 What This Spec Replaces

The save/restore approach (VM-SCOPE-PHASE2, PR #148) is **rejected**. Save/restore treats globals as the source of truth and snapshots them around context boundaries. This spec eliminates the globals entirely — state is structurally owned by the execution context. There is nothing to save or restore.

---

## 2. Background and Motivation

### 2.1 The Problem: Globals in a Nested Execution Model

The doeff VM evaluates algebraic effect programs through a segment/frame execution model. Segments form caller chains, dispatches create handler segments, continuations capture and restore segment state. This is an inherently **nested** execution model — handler dispatch creates a new execution context inside the current one.

Despite this, 6 execution-local fields are held as flat globals on the `VM` struct:

| # | Field | Location | Sites | Corruption Mechanism |
|---|-------|----------|-------|---------------------|
| 1 | `mode: Mode` | `vm.rs` | 128+ | Inner dispatch overwrites instruction register |
| 2 | `current_segment: Option<SegmentId>` | `vm.rs` | 59 | Inner dispatch moves frame pointer |
| 3 | `pending_python: Option<PendingPython>` | `vm.rs` | 15 | Inner Python call overwrites outer's pending slot |
| 4 | `pending_error_context: Option<PyException>` | `vm.rs` | 2 | Inner dispatch `.take()` steals outer's error |
| 5 | `interceptor_eval_depth: usize` | `interceptor_state.rs` | 6 | Outer depth leaks into inner dispatch's guard check |
| 6 | `interceptor_skip_stack: Vec<Marker>` | `interceptor_state.rs` | 6 | Inner skip markers leak to outer, or vice versa |

All 6 share the same failure pattern:
1. Context C1 sets field to state S1
2. Nested context C2 overwrites field to S2
3. C2 completes — no structural restoration exists
4. C1 reads S2 instead of S1 → **corruption**

The save/restore approach (Phase 2) attempted to snapshot globals on dispatch entry and restore on exit. This was rejected because:
- Globals remain the source of truth — corruption windows exist between save and restore
- Restoration requires conservative guards that are hard to get right
- The approach contradicts every established abstract machine model for algebraic effects

### 2.2 The Insight: Segments ARE Execution Contexts

The VM already has a concept that maps directly to "execution context": **Segment**. Segments hold frames (the call stack), form caller chains (the meta-continuation), carry scope chains (handler visibility), and have optional `dispatch_id` (binding to a dispatch context). A segment is created on dispatch entry, continuation resume, interceptor eval, and `WithHandler` body evaluation.

The only thing missing is: segments don't own their execution state. `mode`, `pending_python`, etc. live on the VM singleton instead of on the segment that is currently executing. This spec fixes that.

---

## 3. Theoretical Foundation

The doeff VM was originally conceived as a CESK machine (Felleisen). It evolved segments ad-hoc for delimited continuations. This spec realigns the architecture with established models.

### 3.1 CEK/CESK Machines (Felleisen, 1987)

The CEK machine models computation as a tuple `⟨C, E, K⟩`:
- **C** (Control) — the current expression being evaluated
- **E** (Environment) — variable bindings
- **K** (Continuation) — what to do with the result

CESK adds **S** (Store) for mutable state. All transitions are atomic on the full tuple. There are no globals — the entire machine state is the tuple.

**Mapping to doeff**: `C` = `mode` (the current instruction), `E` = scope chain + handler registry, `K` = segment caller chain + frames, `S` = rust_store/py_store. Today, `C` (`mode`) is a global instead of part of the execution context tuple. This spec moves it onto Segment, restoring the CEK invariant.

### 3.2 Hillerström, Lindley, Atkey — Continuation Passing with Effect Handlers (2017/2020)

Hillerström et al. define an abstract machine for algebraic effects with state `⟨C, E, H⟩`:
- **C** — current computation
- **E** — environment
- **H** — a stack of frames, where each frame is either:
  - **KFrame** — a pure continuation frame (what to do with the result)
  - **HFrame(h, k)** — a handler frame carrying handler `h` and saved continuation `k`

Handler installation pushes an HFrame. Effect dispatch searches H for a matching HFrame, captures the continuation (K frames up to the HFrame), and invokes the handler with the captured continuation. Resume materializes the continuation as new K frames.

**Mapping to doeff**:
- `H` = the segment caller chain. Segments without `dispatch_id` are KFrames. Segments with `dispatch_id` are HFrames.
- The captured continuation = `Continuation` struct (frames_snapshot + scope_chain + marker).
- Handler invocation = `start_dispatch` creating a handler segment.

The critical insight: in Hillerström's model, **each frame in H carries its own computation state**. There is no global instruction register — each frame knows what it was computing. This spec achieves the same by putting `mode` on each Segment.

### 3.3 Multicore OCaml Fibers (Dolan et al., 2017–2024)

OCaml 5.x implements algebraic effects via fibers. Each fiber (handler installation) gets a `stack_info`:

```c
struct stack_info {
    void* sp;                    // stack pointer
    void* exception_ptr;         // current exception handler
    struct stack_handler* handler; // the effect handler
    struct stack_info* parent;   // parent fiber (caller)
};
```

The **one global**: `Caml_state->current_stack` — a pointer to the active fiber. Everything else is on the fiber.

Effect dispatch:
1. Walk `current_stack->parent` chain to find matching handler
2. Copy frames from current stack to a captured continuation (`fiber`)
3. Switch `current_stack` to the handler's fiber
4. Invoke handler with the captured fiber as continuation

Resume:
1. Copy captured fiber's frames onto a new stack
2. Switch `current_stack` to the new stack
3. Deliver the resumed value

**No save/restore.** Parent fiber's state is untouched during child execution because state lives ON the fiber, not in a global that the child overwrites.

**Mapping to doeff**:
- `Caml_state->current_stack` = `vm.current_segment`
- `stack_info` = `Segment`
- `stack_info.parent` = `segment.caller`
- `stack_info.sp` = `segment.frames` (the frame stack)
- `stack_info.handler` = `segment.dispatch_id` → `DispatchContext`

This spec makes the mapping complete by moving execution state (mode, pending_python, etc.) onto Segment, just as OCaml puts `sp` and `exception_ptr` on `stack_info`.

### 3.4 libmprompt (Leijen, 2023)

Leijen's libmprompt implements algebraic effects via "gstacks" — growable stack segments. Each prompt (handler installation) creates a gstack. The implementation uses:

- **Thread-local shadow stack** (`mpe_frame_top`) — linked list of prompt frames, one global pointer
- **Each gstack** — owns its own stack pointer, stack memory, and execution state
- **Resume** = stack switch from current gstack to target gstack

The pattern is identical: one global pointer (the shadow stack top), everything else on the gstack.

### 3.5 Koka (Leijen, Lorenzen, 2020–2024)

Koka uses `kk_context_t` with:
- `evv` — evidence vector for O(1) handler lookup (analogous to scope_chain)
- `yield.conts[]` — accumulates continuation frames during unwinding

Each handler frame in the evidence vector carries its own state. The `yield` mechanism captures per-handler continuations. There is no global "mode" — each handler knows its own execution phase.

### 3.6 The Universal Pattern

Across **all** models:

| Model | Execution Context | Global Pointer | State Ownership |
|-------|-------------------|----------------|-----------------|
| CEK/CESK | The tuple `⟨C, E, K⟩` | None (the tuple IS the state) | Tuple-local |
| Hillerström | Frame in H | Implicit (H is the state) | Per-frame |
| OCaml fibers | `stack_info` | `current_stack` | Per-fiber |
| libmprompt | gstack | `mpe_frame_top` | Per-gstack |
| Koka | evidence frame | `ctx->evv` | Per-frame |
| **doeff (target)** | **`Segment`** | **`current_segment`** | **Per-segment** |

The invariant: **execution-local state is owned by the execution context, not held as a mutable singleton.**

---

## 4. Target Architecture

### 4.1 Target `Segment` Definition

```rust
#[derive(Debug)]
pub struct Segment {
    // --- Existing fields (unchanged) ---
    pub marker: Marker,
    pub frames: Vec<Frame>,
    pub caller: Option<SegmentId>,
    pub scope_chain: Vec<Marker>,
    pub kind: SegmentKind,
    pub dispatch_id: Option<DispatchId>,

    // --- Execution-local state (NEW — moved from VM/InterceptorState) ---

    /// The instruction register for this execution context.
    /// Formerly `VM::mode`. Each segment has its own mode that reflects
    /// what this context is currently computing.
    pub mode: Mode,

    /// Pending Python call state for this execution context.
    /// Formerly `VM::pending_python`. Each segment tracks its own
    /// outstanding Python call, preventing cross-context overwrites.
    pub pending_python: Option<PendingPython>,

    /// Pending error context for exception enrichment.
    /// Formerly `VM::pending_error_context`. Each segment owns its
    /// own error context, preventing `.take()` theft by nested contexts.
    pub pending_error_context: Option<PyException>,

    /// Interceptor eval nesting depth for this execution context.
    /// Formerly `InterceptorState::interceptor_eval_depth`.
    /// Guards against recursive interceptor evaluation within this context.
    pub interceptor_eval_depth: usize,

    /// Interceptor skip markers for this execution context.
    /// Formerly `InterceptorState::interceptor_skip_stack`.
    /// Prevents re-entrant interceptor invocation within this context.
    pub interceptor_skip_stack: Vec<Marker>,
}
```

### 4.2 Target `VM` Definition

```rust
pub struct VM {
    // --- THE one global pointer ---
    pub current_segment: Option<SegmentId>,

    // --- Shared resources (NOT execution-local) ---
    pub segments: SegmentArena,
    pub(crate) dispatch_state: DispatchState,
    pub callbacks: HashMap<CallbackId, Callback>,
    pub consumed_cont_ids: HashSet<ContId>,
    pub handlers: HashMap<Marker, HandlerEntry>,
    pub(crate) interceptor_state: InterceptorState,  // retains non-scoped fields only
    pub rust_store: RustStore,
    pub py_store: Option<PyStore>,
    pub(crate) debug: DebugState,
    pub(crate) trace_state: TraceState,
    pub continuation_registry: HashMap<ContId, Continuation>,
    pub active_run_token: Option<u64>,

    // REMOVED: mode, pending_python, pending_error_context
    // (now on Segment)
}
```

### 4.3 Target `InterceptorState` Definition

`InterceptorState` retains its non-scoped fields (interceptor registry, callback mappings, call metadata, eval callback set). The two execution-local fields move to Segment:

```rust
#[derive(Clone, Default)]
pub(crate) struct InterceptorState {
    interceptors: HashMap<Marker, InterceptorEntry>,
    interceptor_callbacks: HashMap<CallbackId, Marker>,
    interceptor_call_metadata: HashMap<CallbackId, CallMetadata>,
    interceptor_eval_callbacks: HashSet<CallbackId>,
    // REMOVED: interceptor_eval_depth (now on Segment)
    // REMOVED: interceptor_skip_stack (now on Segment)
}
```

Note: `interceptor_eval_callbacks` stays on `InterceptorState` because it maps CallbackIds to registration status — this is a VM-wide registry, not execution-local state. The `increment_eval_depth` / `decrement_eval_depth` methods that currently operate on `InterceptorState.interceptor_eval_depth` will be redirected to operate on the current segment's `interceptor_eval_depth`.

### 4.4 Target `DispatchContext` Definition

No `saved_mode` or `saved_segment` fields. These were added in the rejected Phase 2 (PR #148) and become unnecessary — there is nothing to save because there are no globals to corrupt.

```rust
#[derive(Debug, Clone)]
pub struct DispatchContext {
    pub dispatch_id: DispatchId,
    pub effect: DispatchEffect,
    pub is_execution_context_effect: bool,
    pub handler_chain: Vec<Marker>,
    pub handler_idx: usize,
    pub supports_error_context_conversion: bool,
    pub k_user: Continuation,
    pub prompt_seg_id: SegmentId,
    pub completed: bool,
    pub original_exception: Option<PyException>,
}
```

### 4.5 Target `Continuation` Definition

Continuation already captures `frames_snapshot`, `scope_chain`, `marker`, and `dispatch_id` from the segment at capture time. It must additionally capture the execution-local state:

```rust
#[derive(Debug, Clone)]
pub struct Continuation {
    // --- Existing fields (unchanged) ---
    pub cont_id: ContId,
    pub segment_id: SegmentId,
    pub frames_snapshot: Arc<Vec<Frame>>,
    pub scope_chain: Arc<Vec<Marker>>,
    pub marker: Marker,
    pub dispatch_id: Option<DispatchId>,
    pub started: bool,
    pub program: Option<PyShared>,
    pub handlers: Vec<Handler>,
    pub handler_identities: Vec<Option<PyShared>>,
    pub metadata: Option<CallMetadata>,
    pub parent: Option<Arc<Continuation>>,

    // --- Execution-local state snapshot (NEW) ---
    pub mode: Mode,
    pub pending_python: Option<PendingPython>,
    pub pending_error_context: Option<PyException>,
    pub interceptor_eval_depth: usize,
    pub interceptor_skip_stack: Vec<Marker>,
}
```

When `Continuation::capture()` creates a snapshot, it captures all 5 fields from the segment. When `resume` materializes the continuation into a new segment, it restores all 5 fields.

### 4.6 Access Pattern Migration

Every `self.mode` access becomes `self.current_seg().mode` (read) or `self.current_seg_mut().mode` (write), where:

```rust
impl VM {
    #[inline]
    fn current_seg(&self) -> &Segment {
        let seg_id = self.current_segment.expect("no current segment");
        self.segments.get(seg_id).expect("current segment missing from arena")
    }

    #[inline]
    fn current_seg_mut(&mut self) -> &mut Segment {
        let seg_id = self.current_segment.expect("no current segment");
        self.segments.get_mut(seg_id).expect("current segment missing from arena")
    }
}
```

Similarly for the other 4 fields. The exact accessor pattern will be determined during implementation — the key requirement is that state access always goes through the current segment.

---

## 5. State Transition Rules

### 5.1 Dispatch Entry (`start_dispatch`)

**Before (globals)**:
1. Save `self.mode` and `self.current_segment` somewhere
2. Create handler segment, set `self.current_segment` to it
3. Set `self.mode` for the new dispatch

**After (segment-owned)**:
1. Create handler segment with execution state:
   - `mode: Mode::Deliver(Value::Unit)` (or appropriate initial mode)
   - `pending_python: None`
   - `pending_error_context: original_exception` (if applicable)
   - `interceptor_eval_depth` ← **inherited from parent segment** (re-entrancy guard must be visible)
   - `interceptor_skip_stack` ← **inherited from parent segment** (skip markers must be visible)
2. Set `self.current_segment` to the new handler segment
3. Set `self.current_seg_mut().mode` for the dispatch

**Why no save/restore**: The outer segment's state is untouched. It still has its mode, pending_python, etc. When dispatch completes and `current_segment` returns to the outer segment, the outer segment's state is exactly where it was left.

### 5.2 Dispatch Completion

**Before (globals)**:
1. Pop completed dispatch from stack
2. Restore saved mode and current_segment (Phase 2 approach)

**After (segment-owned)**:
1. Pop completed dispatch from stack
2. Walk caller chain or use continuation to determine which segment to return to
3. Set `self.current_segment` to the parent segment
4. The parent segment's `mode` is already correct — nobody changed it

### 5.3 Continuation Capture

`Continuation::capture(segment)` snapshots the segment's full state:
- `frames_snapshot` ← `segment.frames.clone()`
- `scope_chain` ← `segment.scope_chain.clone()`
- `mode` ← `segment.mode.clone()`
- `pending_python` ← `segment.pending_python.clone()`
- `pending_error_context` ← `segment.pending_error_context.clone()`
- `interceptor_eval_depth` ← `segment.interceptor_eval_depth`
- `interceptor_skip_stack` ← `segment.interceptor_skip_stack.clone()`

### 5.4 Continuation Resume

`resume(continuation, value)` creates a new segment from the snapshot:
1. Create new `Segment` with:
   - `frames` ← `continuation.frames_snapshot`
   - `scope_chain` ← `continuation.scope_chain`
   - `mode` ← `Mode::Deliver(value)` (the resumed value overrides the captured mode)
   - `pending_python` ← `continuation.pending_python` (or `None` if fresh)
   - `pending_error_context` ← `continuation.pending_error_context`
   - `interceptor_eval_depth` ← `continuation.interceptor_eval_depth`
   - `interceptor_skip_stack` ← `continuation.interceptor_skip_stack`
2. Set `self.current_segment` to the new segment

Note: `mode` is set to `Mode::Deliver(value)` on resume because the continuation is being given a value. The captured mode (what the segment was doing when captured) is superseded by the resume action.

### 5.5 Continuation Transfer

Same as Resume (§5.4) except `caller` is set to `None` (severing the caller chain) and the delivered mode depends on the transfer semantics.

### 5.6 Interceptor Eval Entry

When an interceptor callback is invoked:
1. Create body segment (already happens via `prepare_with_intercept`)
2. Body segment gets execution state:
   - `mode`: set by the interceptor eval setup
   - `pending_python: None`
   - `pending_error_context: None`
   - `interceptor_eval_depth` ← **inherited from parent segment** (guards against recursive interceptor evaluation)
   - `interceptor_skip_stack` ← **inherited from parent segment** (prevents re-entrant interceptor invocation)
3. Set `self.current_segment` to the body segment

Interceptor guard state is inherited by value (copied, not aliased). The child sees the parent's active guards so it won't re-enter a skipped interceptor. But the child's modifications (push/pop) do not affect the parent — isolation is preserved. This matches the OCaml fiber model where dynamic guard state travels with the continuation context.

### 5.7 WithHandler Body Entry

Body segment inherits the parent's interceptor guard state (`interceptor_eval_depth`, `interceptor_skip_stack`) by copy. Transient state (`pending_python`, `pending_error_context`) is fresh. `mode` is inherited since `WithHandler` doesn't change what's being computed, just what handlers are visible.

### 5.8 Segment Deallocation

When a segment is freed (e.g., during throw propagation), its execution-local state is dropped with it. No cleanup of globals needed — there are no globals.

---

## 6. Invariants

### INV-1: Single Source of Truth

For each execution-local field, the ONLY location it can be read from or written to during a step is `self.segments[self.current_segment].<field>`. There is no secondary copy on the VM struct.

### INV-2: Context Isolation

A nested execution context (dispatch, interceptor eval, continuation resume) CANNOT observe or modify the execution-local state of its parent context. The parent's segment is not the current segment, so its fields are unreachable through the standard access pattern (`current_seg()`/`current_seg_mut()`).

### INV-3: Automatic Preservation

When a nested context completes and `current_segment` returns to the parent, the parent's execution-local state is exactly as it was before the nested context began. This is not achieved by save/restore — it is structural: nobody modified the parent's segment.

### INV-4: One Global

`current_segment: Option<SegmentId>` is the ONLY execution-local mutable singleton on the VM struct. All other execution-local state lives on `Segment`. This mirrors OCaml's `Caml_state->current_stack`.

### INV-5: Continuation Completeness

`Continuation::capture()` captures ALL execution-local state from the segment. `resume()` restores ALL of it (with `mode` overridden by the resume value). No execution-local state is "forgotten" across capture/resume.

### INV-6: Appropriate State on New Contexts

Newly created segments start with state appropriate to their role:
- **Transient state** (`pending_python`, `pending_error_context`): always **fresh** (None). These are in-flight call state that doesn't carry across context boundaries.
- **Interceptor guard state** (`interceptor_eval_depth`, `interceptor_skip_stack`): **inherited from parent** by value copy. Guards must be visible to child contexts to prevent re-entrancy, but child modifications must not affect the parent (copy, not alias).
- **mode**: set by the creating operation (e.g., `Deliver(value)` on resume, initial mode on dispatch).

Continuation resumes restore all 5 fields from the snapshot, with `mode` overridden by the resume value.

---

## 7. What Does NOT Change

- **DoCtrl vocabulary**: Same variants, same semantics.
- **StepEvent / PythonCall**: Same driver protocol.
- **Mode enum**: Same 4 variants (Deliver, Throw, HandleYield, Return). Only its location changes (VM → Segment).
- **Handler dispatch protocol**: `start_dispatch`, handler chain walking, `Delegate`/`Pass`/`Resume`/`Transfer` — all same semantics.
- **Python bridge**: `pyvm.rs` interface unchanged. Python doesn't see segment internals.
- **SegmentArena**: Same allocation/deallocation. Segments just have more fields.
- **DispatchState**: Same dispatch stack management. `DispatchContext` loses `saved_mode`/`saved_segment` (if present from Phase 2).

---

## 8. Migration Strategy

### 8.1 Approach: Single Atomic Migration

All 6 fields move in one change. This avoids intermediate states where some fields are on Segment and some are globals — such states would require the step loop to read from two different locations depending on the field, creating confusion and bugs.

### 8.2 Mechanical Transformation

The migration is largely mechanical:

1. Add 5 fields to `Segment` struct
2. Update `Segment::new()` and `Segment::new_prompt()` to initialize them
3. Remove `mode`, `pending_python`, `pending_error_context` from `VM`
4. Remove `interceptor_eval_depth`, `interceptor_skip_stack` from `InterceptorState`
5. Replace all `self.mode` → `self.current_seg_mut().mode` (or `current_seg().mode`)
6. Replace all `self.pending_python` → `self.current_seg_mut().pending_python`
7. Replace all `self.pending_error_context` → `self.current_seg_mut().pending_error_context`
8. Redirect interceptor eval depth/skip methods to current segment
9. Update `Continuation::capture()` to snapshot the new fields
10. Update continuation resume to restore the new fields
11. Update `clear_for_run()` to clear segment-level state (or rely on segment recreation)

### 8.3 Borrow Checker Considerations

The main challenge: `self.current_seg_mut()` borrows `self.segments` mutably. If the same method also needs to read `self.handlers` or `self.dispatch_state`, the borrow checker will complain.

Mitigation strategies:
- **Extract into locals**: `let seg = &mut self.segments[seg_id]; seg.mode = ...;` before accessing other fields
- **Split borrows**: Access `self.segments` and `self.handlers` as separate fields (Rust allows borrowing different struct fields simultaneously)
- **Helper methods**: Methods on `SegmentArena` that take the segment ID and operate directly

This is a mechanical challenge, not an architectural one. The Rust borrow checker will guide the correct split.

---

## 9. Architectural Enforcement (Semgrep)

After migration, the invariants in §6 are enforced by both the Rust type system and semgrep rules in `.semgrep.yaml`.

**Type system enforcement**: If `mode` is not a field on `VM`, then `self.mode` won't compile. This prevents direct access regressions automatically.

**Semgrep enforcement**: Prevents someone from adding the fields back to the wrong struct. 8 rules under category `vm-scope-structural`:

| Rule ID | What it prevents | File guarded |
|---------|-----------------|--------------|
| `vm-scope-no-mode-on-vm-struct` | `pub mode: Mode` on VM | `vm.rs` |
| `vm-scope-no-pending-python-on-vm-struct` | `pub pending_python: Option<PendingPython>` on VM | `vm.rs` |
| `vm-scope-no-pending-error-context-on-vm-struct` | `pub pending_error_context: Option<PyException>` on VM | `vm.rs` |
| `vm-scope-no-eval-depth-on-interceptor-state` | `interceptor_eval_depth: usize` on InterceptorState | `interceptor_state.rs` |
| `vm-scope-no-skip-stack-on-interceptor-state` | `interceptor_skip_stack: Vec<Marker>` on InterceptorState | `interceptor_state.rs` |
| `vm-scope-no-saved-mode-on-dispatch-context` | `saved_mode: Mode` on DispatchContext | `dispatch.rs` |
| `vm-scope-no-saved-segment-on-dispatch-context` | `saved_segment: Option<SegmentId>` on DispatchContext | `dispatch.rs` |

Rules run as part of `make lint-semgrep`. They fire on `pub mode: Mode` in `vm.rs`, `interceptor_eval_depth: usize` in `interceptor_state.rs`, etc. — catching field additions at PR review time before they reach main.

The last two rules additionally prevent the rejected save/restore approach (PR #148) from being reintroduced.

---

## 10. Open Questions

### Q1: Should `mode` on Resume Always Be `Deliver(value)`?

When resuming a continuation, the spec says `mode = Deliver(value)`. But what about `Transfer` which might want `Throw`? Need to verify all resume/transfer paths.

### Q2: Continuation Capture for `pending_python`

When a continuation is captured mid-Python-call (pending_python is Some), should the continuation snapshot include it? The current design says yes. But if the Python call is in-flight, capturing it might not make sense. Need to verify whether capture ever happens with `pending_python = Some(...)`.

### Q3: `interceptor_eval_callbacks` Location

`InterceptorState::interceptor_eval_callbacks` is a `HashSet<CallbackId>` that tracks which callbacks are interceptor eval callbacks. It's a VM-wide registry (callback IDs are unique). Keeping it on `InterceptorState` (not Segment) seems correct, but the `register_eval_callback` method currently also increments `interceptor_eval_depth` — this coupling needs to be split (registry stays on InterceptorState, depth increment goes to current segment).

---

## 11. References

1. Felleisen, M. (1987). *The Calculus of Lambda-v-CS Conversion: A Syntactic Theory of Control and State in Imperative Higher-Order Programming Languages.* — CEK/CESK machines.

2. Hillerström, D., Lindley, S., Atkey, R. (2017). *Effect Handlers via Generalised Continuations.* — `⟨C, E, H⟩` abstract machine for algebraic effects.

3. Hillerström, D., Lindley, S. (2020). *Continuation Passing Style for Effect Handlers.* — CPS translation for the handler stack model.

4. Dolan, S., White, L., Sivaramakrishnan, K.C., Yallop, J., Madhavapeddy, A. (2017). *Concurrent System Programming with Effect Handlers.* — OCaml fiber model.

5. Sivaramakrishnan, K.C., et al. (2021). *Retrofitting Effect Handlers onto OCaml.* — Production implementation of fibers in OCaml 5.

6. Leijen, D. (2023). *libmprompt — Efficient Algebraic Effect Handlers in C.* — gstack-based implementation.

7. Leijen, D., Lorenzen, A. (2020–2024). *Koka: A Function-Oriented Language with Effect Types.* — Evidence-passing translation, `kk_context_t`.

8. Danvy, O., Filinski, A. (1990). *Abstracting Control.* — Delimited continuations, shift/reset, meta-continuation model `⟨C, E, k, m⟩`.
