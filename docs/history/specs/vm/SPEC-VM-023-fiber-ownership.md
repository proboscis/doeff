# SPEC-VM-023: Fiber Ownership and Orphan Reclamation

**Status:** Draft
**Created:** 2026-04-25
**Depends on:** ISSUE-VM-001, SPEC-VM-020, SPEC-VM-021, PR #394
**Scope:** PR G1

## Problem

PR G0 removed the VM's `mode` and `last_error_context` registers. The remaining
mutable-state violation from ISSUE-VM-001 is orphan reclamation:

```rust
VM.orphan_queue: Arc<Mutex<Vec<FiberId>>>
Continuation.orphan_queue: Option<OrphanQueue>
```

Today a dropped, unconsumed `Continuation` pushes its detached fiber head into
the queue from `Drop`. `VM::step` drains that queue and frees the fiber chain
from the arena.

This works mechanically, but it violates the VM invariant:

1. `Continuation::drop` mutates VM-owned state through a shared pointer.
2. The queue is a hidden VM register, even though it is presented as a GC
   mechanism.
3. Reclamation timing depends on an out-of-band side channel instead of fiber
   ownership.

G1 must remove the queue without weakening one-shot continuation semantics.

## Current Ownership Model

The current code has these owners:

| Object | Owner |
| --- | --- |
| Live executing fibers | `VM.segments: FiberArena` |
| Detached continuation head/tail | `Continuation { head, last_fiber }` |
| Python-visible continuation | `PyK { continuation: Option<OwnedControlContinuation> }` |
| Orphan notification | `Arc<Mutex<Vec<FiberId>>>` shared by VM and continuations |

`Continuation` does not own the detached fibers themselves. It owns only two
arena IDs. Therefore, if a `Continuation` is dropped without being consumed,
the arena still owns the detached fibers but no semantic root can reach them.
The orphan queue exists to tell the arena what to free later.

## Required Invariants

After G1:

1. `VM` has no `orphan_queue` field.
2. `Continuation` has no queue, VM pointer, arena pointer, `Arc<Mutex<_>>`, or
   other shared mutable reclamation channel.
3. Dropping an unconsumed continuation does not mutate VM state.
4. A live `PyK` remains first-class. It may be held by Python code and resumed
   later, so reclamation must not assume that only the active VM fiber chain is
   live.
5. Fibers reachable from a live continuation must not be freed or reused.
6. Fibers reachable from no VM root and no continuation root must eventually
   release Python payload references.

## Design Options

### Option A: Keep Arena Ownership, Replace Queue with Mark-Sweep

The VM would periodically walk roots, mark reachable arena fibers, and free
unmarked fibers.

Roots are:

- `VM.current_segment`
- all live `Continuation` heads

This removes the queue only if live continuations can be enumerated without a
new mutable registry. The current bridge cannot do that: `PyK` is a Python
object and can be held in generator locals or user data outside the VM's arena
walk. A sweep rooted only at `current_segment` would free a still-live
continuation.

Adding a live-`PyK` registry would replace `orphan_queue` with another VM-level
mutable side table. That does not satisfy ISSUE-VM-001.

**Decision:** reject for G1 unless a future design makes continuation roots
structurally enumerable without a registry.

### Option B: Continuation Owns Detached Fibers

On perform, the VM removes the detached chain from arena ownership and moves
the fiber values into the `Continuation`. On resume/pass, the VM reattaches the
owned chain to the arena. If the continuation is dropped, Rust drops the owned
fiber values directly; no VM callback or queue is needed.

This is the clean ownership model:

```rust
pub struct Continuation {
    chain: Option<DetachedFiberChain>,
}

pub struct DetachedFiberChain {
    head: FiberId,
    last_fiber: FiberId,
    fibers: Vec<DetachedFiber>,
}

pub struct DetachedFiber {
    old_id: FiberId,
    fiber: Fiber,
}
```

The exact storage shape may change during implementation, but ownership must
not: while detached, the continuation owns the fiber values.

**Decision:** G1 should implement this direction.

### Option C: Cosmetic Queue Replacement

Replacing `Arc<Mutex<Vec<FiberId>>>` with `Rc<RefCell<Vec<FiberId>>>`, a
thread-local, an atomic flag, or a weak registry keeps the same hidden mutable
channel.

**Decision:** reject.

## Target Semantics

### Perform

1. Find the matching handler boundary.
2. Detach the body-through-boundary chain from the active arena chain.
3. Move the detached fiber values into `Continuation`.
4. Switch `current_segment` to the handler parent.
5. Pass `Value::Continuation(k)` to the handler.

The VM keeps no copy of `k`.

### Resume / Transfer

1. Destructively take the chain from `Continuation`.
2. Reattach the chain to the arena with the current fiber as the parent of the
   chain tail.
3. Switch `current_segment` to the reattached head.
4. Deliver the value or exception.

A second resume still fails through `Option::take()`.

### Pass / Reperform

1. Destructively take or mutably own the existing detached chain.
2. Append the current handler chain to it.
3. Search for the next outer handler.
4. Pass the extended continuation to that handler.

No placeholder continuation may be created solely to satisfy type shape.

### Drop

Dropping an unconsumed `Continuation` drops its owned detached fiber chain.
This releases frames, streams, and Python payload references immediately from
normal Rust ownership. It does not call into `VM`.

## Arena API Requirements

G1 will need arena operations that make ownership explicit:

```rust
impl FiberArena {
    fn detach_chain(&mut self, head: FiberId, last: FiberId) -> Result<DetachedFiberChain, VMError>;
    fn attach_chain(
        &mut self,
        chain: DetachedFiberChain,
        tail_parent: Option<FiberId>,
    ) -> Result<FiberId, VMError>;
}
```

Implementation details to settle in code:

- Whether detached chains re-use their original `FiberId`s or receive fresh IDs
  on reattach.
- How `GetTraceback(k)` and `GetHandlers(k)` inspect a detached chain while it
  is not in the arena.
- Whether the arena needs a reserved-slot state during detachment. A reserved
  slot is acceptable only if it stores no `Fiber` and therefore cannot retain
  Python payloads.

## TDD and Guard Plan

Before implementation, add failing tests and guards:

1. Runtime regression:
   - un-xfail or clone the existing bounded-memory tests in
     `tests/test_vm_memory_leak.py`.
   - Add a focused Rust test that drops an unconsumed `Continuation` and
     verifies the detached fiber payloads are released without calling
     `VM::step`.
2. Architecture tests:
   - `VM` must not contain `orphan_queue`.
   - `Continuation` must not contain `orphan_queue`, `OrphanQueue`, `Arc`,
     `Mutex`, or an arena/VM pointer.
   - `Continuation::drop` must not enqueue or lock.
3. Semgrep guards:
   - ban `orphan_queue` in `packages/doeff-vm-core/src`.
   - ban `Arc<Mutex<Vec<FiberId>>>` in VM core.
   - ban `Continuation::new(..., vm.orphan_queue.clone())`-style constructors.

The tests should fail on the current code before implementation.

## Acceptance Criteria

G1 is complete when:

1. `rg "orphan_queue|OrphanQueue" packages/doeff-vm-core/src` returns no
   production-code hits.
2. `Continuation` owns detached fiber values or an equivalent move-only
   detached-chain object.
3. Dropping a live, unconsumed `PyK` does not require the VM to take another
   step to release detached fiber payloads.
4. Existing continuation behavior remains intact:
   - resume is one-shot
   - pass/reperform preserves handler ordering
   - traceback and handler introspection still work for continuation roots
5. Root pytest does not regress from the post-G0 baseline.
6. `make lint` is no worse than the known pre-existing pyright/import failures.

## Non-Goals

- G1 does not implement SPEC-VM-022 pass reason trails.
- G1 does not redesign Python generator state retention except where retained
  payloads are caused by detached fiber ownership.
- G1 does not reintroduce continuation cloning, `ContId`, or shared consumed
  flags.
