# SPEC-VM-020: OCaml 5 Runtime Alignment

**Status:** Draft
**Created:** 2026-03-23
**Motivation:** The doeff VM has diverged from OCaml 5's effect handler runtime to the
point where changes cascade unpredictably and regressions are constant. This spec defines
the exact alignment target based on OCaml 5's actual C implementation, not abstractions.

## Core Principle

**The fiber chain IS the state. The stack is the only mutable thing.**

In OCaml 5, there are no side-tables, no accumulated tracking, no identity registries.
The runtime state is fully described by:
1. Which fibers exist (the arena)
2. How they're linked (parent pointers)
3. Where execution is (current_stack, sp)

Everything else — handler visibility, dispatch context, scope — is **derived** by walking
the chain. If you can't derive it from the chain, you have a design bug.

## OCaml 5 Actual Data Structures

Source: `runtime/caml/fiber.h`, `runtime/fiber.c`, `runtime/amd64.S`

### stack_info (the Fiber)

```c
struct stack_info {
    void* sp;                    // stack pointer when suspended
    void* exception_ptr;         // exception handler chain when suspended
    struct stack_handler* handler; // handler at TOP of stack memory
    int cache_bucket;            // freelist index for recycling
    size_t size;                 // mmap size
    uintnat magic;               // debug: always 42
    int64_t id;                  // unique fiber id (monotonic counter)
};
```

**That's it.** No mode, no dispatch_id, no trace_state, no pending_error_context,
no interceptor depth. A fiber is a stack pointer, an exception chain, and a handler.

### stack_handler (the Handler Delimiter)

Lives at the TOP of the fiber's stack memory:

```c
struct stack_handler {
    value handle_value;     // called when child fiber returns normally
    value handle_exn;       // called when child fiber raises uncaught exception
    value handle_effect;    // called when child fiber performs an effect
    struct stack_info* parent;  // parent fiber
};
```

**The parent pointer is ON the handler, not the fiber.** This is important: the handler
and the parent link are the same concept — "who installed this handler" IS "who is my
parent." In doeff terms: `fiber.parent` and `fiber.handler` are architecturally one thing.

### Continuation

A GC-managed block with tag `Cont_tag (245)`:

```
field[0]: Val_ptr(stack_info*)   // head of captured fiber chain (tagged as int, not GC-traced)
field[1]: last_fiber             // tail of chain (for O(1) append during reperform)
```

A continuation IS the captured fiber chain. No cont_id, no dispatch_id, no metadata.
One-shot is enforced by atomically swapping field[0] to NULL — a destructive read.

### Domain State (the "VM Registers")

```c
struct caml_domain_state {
    struct stack_info* current_stack;  // currently executing fiber
    value* exn_handler;                // current exception trap
    // ... GC state, minor heap, etc.
};
```

The VM has exactly one register that matters: `current_stack`. Everything else is
either the GC (not our concern) or derived from the fiber chain.

## The Five Operations

### 1. match_with (install handler)

```
Allocate new fiber F with handler H
F.handler.parent = current_stack
current_stack = F
Execute body on F
```

**doeff equivalent:** `WithHandler(handler, program)` → allocate fiber, set parent, push.

### 2. perform (yield effect)

```
old = current_stack
parent = old.handler.parent
if parent == NULL: raise Effect.Unhandled

// Capture: detach old from parent
old.handler.parent = NULL

// Create continuation = pointer to old (the detached chain)
cont = alloc(old, last_fiber=old)

// Switch to parent
current_stack = parent
Call parent.handler.handle_effect(effect, cont, last_fiber)
```

**What happens:** The performing fiber is detached from its parent. That's it. No
dispatch_id is created. No DispatchTrace is accumulated. No ProgramDispatch is stored.
The effect, the continuation, and the handler closure are passed as function arguments.

**doeff equivalent:** `yield SomeEffect()` → walk chain, detach, call handler.

### 3. continue k v (resume)

```
fiber = atomic_swap(cont.field[0], NULL)  // one-shot: destructive read
if fiber == NULL: raise Continuation_already_resumed

// Reattach: link continuation tail to current stack
last_fiber.handler.parent = current_stack

// Switch to resumed fiber
current_stack = fiber
Deliver v to fiber's suspended sp
```

**What happens:** The continuation's fibers are reattached to the current chain by
setting one parent pointer. Then execution switches. No cont_id lookup. No registry.
No consumed_set check.

**doeff equivalent:** `Resume(k, v)` → reattach fibers, deliver value.

### 4. reperform (pass/delegate effect)

```
// Current handler doesn't handle this effect.
// Append self to the continuation chain:
last_fiber.handler.parent = current_stack
last_fiber = current_stack

// Detach self from parent:
parent = current_stack.handler.parent
current_stack.handler.parent = NULL

// Switch to parent:
current_stack = parent
Call parent.handler.handle_effect(effect, cont, last_fiber)
```

**What happens:** The non-handling fiber appends itself to the continuation chain.
Then the effect is re-performed at the parent. The continuation grows by one fiber.
No DispatchId. No separate Pass vs Delegate distinction at the runtime level.

**doeff equivalent:** `Pass()` or `Delegate()` → append fiber to continuation, re-perform at parent.

### 5. Fiber completion (return)

```
old = current_stack
parent = old.handler.parent
hval = old.handler.handle_value

// Switch to parent:
current_stack = parent
Free old  // return to freelist

// Call return handler:
hval(return_value)
```

**What happens:** The completed fiber is freed and its parent's `handle_value` is called.

## doeff Divergences from OCaml 5

### CRITICAL: Identity Tracking That Shouldn't Exist

| doeff concept | OCaml 5 equivalent | Why it's wrong |
|---|---|---|
| `DispatchId(u64)` | Does not exist | OCaml 5 has no dispatch identity. An effect is performed, a handler is called. There is no "dispatch" as a trackable entity. |
| `ContId(u64)` | Does not exist | A continuation IS the fiber chain. Its identity is the head pointer. |
| `Marker(u64)` | The handler's position in the chain | In OCaml 5, a handler is identified by its position (which fiber it's on). No separate marker. |
| `VarId.owner_segment` | Does not exist | Heap cells are addressed by pointer, not by owner. |

**Impact:** DispatchId forces the existence of ProgramDispatch (to store it),
DispatchTrace (to record what happened to it), TraceState (to accumulate traces),
and complex lookup functions (to find dispatch context by walking topology and
matching IDs). Remove DispatchId and all of this machinery dissolves.

### CRITICAL: Accumulated State That Should Be Derived

| doeff state | OCaml 5 | How to derive |
|---|---|---|
| `ProgramDispatch` on Frame | Does not exist | The "dispatch context" IS the handler call. The handler closure received (effect, k) as arguments. |
| `DispatchTrace` in ProgramDispatch | Does not exist | Tracing is a debugger concern, not a runtime concern. If needed, walk the chain. |
| `TraceState` on VM | Does not exist | Walk the fiber chain to get the current stack trace. |
| `pending_error_context` on Fiber | `exception_ptr` on stack_info | OCaml 5 stores the exception chain pointer, not rich error context. If needed, this is a VM register (one active error at a time), not per-fiber. |
| `handler_type_match_cache` on VM | Does not exist | OCaml 5 handlers receive ALL effects and pattern-match. No type filtering at the runtime level. |
| `completed_segment` on VM | Does not exist | A completed fiber is freed. The parent's handle_value is called. |

### doeff Extensions (Not in OCaml 5, Acceptable If Clean)

| doeff feature | Status | Rule |
|---|---|---|
| Interceptors (`InterceptSpec`) | Extension | Must not add fields to Fiber. Must be layered as a special handler. |
| Effect masking (`MaskSpec`) | Extension | Must not add fields to Fiber. Must be layered as a special handler. |
| Typed handlers (`types` filter) | Extension | Must not change perform/continue semantics. Pattern-match after dispatch. |
| VarStore (heap) | Equivalent to OCaml heap | OK. `ref` cells in OCaml are heap-allocated mutable boxes. |
| IRStream (generator) | Equivalent to OCaml bytecode | OK. OCaml interprets bytecode, doeff interprets IR from generators. |

### Rule for Extensions

An extension is acceptable if and only if:
1. Removing it leaves a correct OCaml 5 effect handler runtime
2. It does not add fields to Fiber (beyond frames, parent, handler)
3. It does not add accumulated state to the VM
4. It does not require identity tracking (no new ID types)
5. Its state is entirely on boundary fibers or in the heap (VarStore)

## Target Architecture

### Fiber (3 fields)

```rust
struct Fiber {
    frames: Vec<Frame>,
    parent: Option<FiberId>,
    handler: Option<Handler>,  // None = normal fiber, Some = handler delimiter
}
```

`Handler` replaces `FiberKind::Boundary(FiberBoundary)`. It contains the handler
closure and optionally the extension specs (intercept, mask). But these are on the
Handler, not the Fiber.

Interceptor runtime state (`interceptor_eval_depth`, `interceptor_skip_stack`) moves
to the Handler or to the heap (VarStore). `pending_error_context` becomes a VM register.
`pending_program_dispatch` is eliminated (dispatch has no persistent state).

### VM (5 registers)

```rust
struct VM {
    arena: FiberArena,          // fiber pool
    current_fiber: FiberId,     // the running fiber
    heap: VarStore,             // mutable ref cells (OCaml heap equivalent)
    mode: Mode,                 // Deliver | Throw | HandleYield | Return
    pending_python: Option<PendingPython>,  // GIL boundary (doeff-specific)
}
```

No caches. No trace_state. No debug. No completed_segment. No py_store.
No handler_type_match_cache. No topology epochs.

Caches are acceptable as a separate, non-architectural concern (behind a
CacheLayer wrapper or similar), but they must not appear as VM struct fields.

### Continuation (2 fields)

```rust
struct Continuation {
    fiber: FiberId,        // head of captured chain
    last_fiber: FiberId,   // tail for O(1) append (OCaml has this too)
}
```

No cont_id. No consumed flag as a field — one-shot is enforced by destructive take
(Option::take or similar). No metadata. No dispatch_id.

### Frame (minimal)

```rust
enum Frame {
    Program { stream: IRStream, metadata: Option<CallMetadata> },
    LexicalScope { bindings: HashMap<Key, Value> },
    EvalReturn(EvalReturnContinuation),
    MapReturn { mapper: PyShared },
    FlatMapBind { ... },
}
```

No `dispatch: Option<ProgramDispatch>`. No `handler_kind`. No `ProgramDispatch`.
The dispatch context is not stored — it's the handler call itself.

### Perform (doeff)

```rust
fn perform(&mut self, effect: Effect) {
    // Walk parent chain to find handler
    let (handler_fiber, handler) = self.walk_chain_for_handler(&effect);

    // Detach: sever parent link
    let captured_head = self.current_fiber;
    let captured_tail = /* fiber just before handler_fiber */;
    self.set_parent(captured_head, None); // detach

    // Create continuation
    let k = Continuation { fiber: captured_head, last_fiber: captured_tail };

    // Switch to handler fiber
    self.current_fiber = handler_fiber;

    // Call handler
    self.deliver_to_handler(handler, effect, k);
}
```

No DispatchId created. No ProgramDispatch stored. No trace recorded.

### Resume (doeff)

```rust
fn resume(&mut self, k: Continuation, value: Value) {
    // One-shot: destructive take
    // (Continuation is consumed by this call — move semantics)

    // Reattach: link tail to current
    self.set_parent(k.last_fiber, Some(self.current_fiber));

    // Switch to resumed fiber
    self.current_fiber = k.fiber;

    // Deliver value
    self.mode = Mode::Deliver(value);
}
```

No cont_id lookup. No consumed_set check. The continuation is moved, not cloned.

## Migration Strategy

Each step must:
1. Be independently merge-able
2. Pass the FULL test suite (zero regressions)
3. Be verifiable by a behavioral test (not source-pattern matching)

### Phase 1: Remove DispatchId

This is the keystone. DispatchId forces ProgramDispatch, DispatchTrace, TraceState,
and the complex dispatch-context-finding code. Remove it and the cascade dissolves.

Steps:
1. Make dispatch work without DispatchId (pass effect+continuation as values)
2. Remove ProgramDispatch from Frame
3. Remove DispatchTrace
4. Remove TraceState accumulated maps
5. Remove DispatchId type

### Phase 2: Remove ContId

Steps:
1. Make one-shot work by move semantics (destructive take), not by ContId lookup
2. Remove ContId from Continuation
3. Remove continuation_registry if any remnants exist

### Phase 3: Shrink Fiber to 3 fields

Steps:
1. Move pending_error_context to VM register
2. Move interceptor state to Handler (on boundary fibers only)
3. Eliminate pending_program_dispatch (consequence of Phase 1)
4. Fiber = frames + parent + handler

### Phase 4: Shrink VM to 5 registers

Steps:
1. Extract caches to a separate non-architectural layer
2. Extract debug/trace to a separate layer
3. Remove completed_segment, py_store, active_run_token
4. VM = arena + current_fiber + heap + mode + pending_python

## What This Spec Does NOT Cover

- **Tracing/debugging:** A separate observability layer can walk the chain on demand.
  It must not accumulate state in the VM or on fibers.
- **Performance caches:** Acceptable behind a CacheLayer, must not be VM fields.
- **Python bridge specifics:** pending_python is doeff-specific (GIL boundary).
  It's the one register OCaml doesn't need.
- **Scheduler:** Task scheduling is built on top of effects (Spawn is an effect).
  It's a handler, not a VM feature.
