# SPEC-VM-021: Single-Owner Continuations

**Status:** Draft
**Created:** 2026-03-23
**Depends on:** SPEC-VM-020 (OCaml 5 alignment), PR #369 (boundary fiber context)
**Replaces:** Failed Phase 2 attempt (PR #371, rejected)

## Problem

PR #371 tried to remove ContId but made the architecture WORSE:
- Introduced `Arc<AtomicBool>` shared consumed flag (was plain `bool`)
- Brought back `captured_caller` field (was removed in PR #367)
- Added `fork_handle()` method (new complexity)
- 4 copies of each continuation still exist simultaneously

The root cause: the VM clones continuations into multiple places, then needs
shared state to coordinate consumed tracking across copies. Removing ContId
without removing the cloning just shifts the coordination mechanism.

## OCaml 5 Model

A continuation has ONE owner. Period.

```
perform:
  k = capture(fibers)        ← k created
  handler(effect, k)         ← k passed to handler, handler is sole owner
  runtime keeps NOTHING      ← no copy anywhere

continue k v:
  fibers = atomic_swap(k, NULL)  ← destructive take, k is gone
  reattach(fibers)

handler throws without continue:
  k is on handler's stack frame
  stack unwinds → k dropped → fibers freed by GC
```

## Why doeff Currently Needs Multiple Owners

The VM clones the continuation into 4 places:

```
┌─────────────────────────────────────────────┐
│ Where                    │ Why              │
├─────────────────────────────────────────────┤
│ PyK (handler argument)   │ Handler uses it  │
│ ProgramDispatch.origin   │ Traceback display│
│ ProgramDispatch.handler_ │ Error cleanup    │
│   continuation           │                  │
│ boundary fiber pending_  │ Pass/Delegate    │
│   continuation           │                  │
└─────────────────────────────────────────────┘
```

Each "Why" can be solved without a copy:

```
┌─────────────────────────────────────────────────────────────┐
│ Need                │ Solution without copy                 │
├─────────────────────────────────────────────────────────────┤
│ Handler uses k      │ PyK holds the ONLY copy (sole owner) │
│ Traceback display   │ Walk fiber chain to find detached     │
│                     │ fibers (they have parent=None)        │
│ Error cleanup       │ Read PyK from handler generator on    │
│ (free orphan fibers)│ error, or read boundary fiber's       │
│                     │ pending_continuation                  │
│ Pass/Delegate       │ Take k FROM PyK (handler is done)     │
│                     │ PR #369 boundary context is backup    │
└─────────────────────────────────────────────────────────────┘
```

## Target Design

### Continuation (2 fields, move-only)

```rust
pub struct Continuation {
    fibers: Vec<FiberId>,      // the captured fiber chain
    last_fiber: FiberId,       // tail for O(1) append (OCaml 5 has this)
}

// NO Clone, NO clone_handle(), NO fork_handle()
// NO Arc<AtomicBool>, NO consumed flag
// NO cont_id, NO fiber_id identity field
```

One-shot is enforced by `Option<Continuation>`:
- PyK holds `Option<Continuation>`
- `take()` returns `Some(k)` first time, `None` after
- No coordination needed because there's only ONE place to take from

### PyK (Python wrapper, sole owner)

```rust
#[pyclass]
pub struct PyK {
    continuation: Option<Continuation>,   // Some = live, None = consumed
    pending: Option<PendingContinuation>, // unstarted program (separate concept)
}

impl PyK {
    // Destructive take — OCaml 5's atomic_swap equivalent
    pub fn take(&mut self) -> Option<Continuation> {
        self.continuation.take()
    }

    // Check without consuming
    pub fn is_consumed(&self) -> bool {
        self.continuation.is_none() && self.pending.is_none()
    }
}
```

### ProgramDispatch → eliminated or minimal

ProgramDispatch currently exists to hold dispatch identity (origin_cont_id),
the origin continuation (clone), and the handler continuation (clone). All
three are unnecessary:

```
Before (current):
  Frame::Program {
      stream,
      metadata,
      handler_kind,
      dispatch: Option<ProgramDispatch>,  ← holds copies of continuation
  }

After:
  Frame::Program {
      stream,
      metadata,
      handler_kind,
      // dispatch: GONE
      // effect stored on boundary fiber (PR #369)
      // continuation lives in PyK only
  }
```

If traceback display needs the effect repr, it's on the boundary fiber.
If error cleanup needs the continuation, it's in PyK or on the boundary fiber.

### Operations

#### perform (yield Effect)

```
1. Walk chain from current_fiber, find handler boundary fiber
2. Detach fibers between current and handler → Continuation { fibers, last_fiber }
3. Store effect on boundary fiber (PR #369, already done)
4. Store continuation reference on boundary fiber (for error cleanup only)
5. Create PyK with the continuation → PyK is sole owner
6. Start handler IRStream, passing (effect_pyobj, pyk) as arguments
7. VM keeps NO copy of the continuation
```

```
  BEFORE:                          AFTER:
  F3 → F2 → F1(hdlr) → F0        F1(hdlr) → F0
                                     ▲ current
                                     │
                                   boundary fiber stores:
                                     effect (for Pass)
                                     weak ref to PyK (for cleanup)

                                   PyK (sole owner):
                                     continuation = Some([F3, F2])
```

#### Resume(k, v)

```
1. Handler yields Resume(k, v)
2. VM calls pyk.take() → returns Some(continuation)
3. Reattach: last_fiber.parent = current_fiber
4. Switch to continuation.fibers[0]
5. Deliver v
6. PyK now has continuation = None
```

```
  Handler yields Resume(k, 42):

  pyk.take() → Some([F3, F2])     PyK { continuation: None }

  Reattach:
  F3 → F2 → F1(hdlr) → F0
  ▲ current, delivers 42
```

#### Resume(k, v) AGAIN (one-shot violation)

```
1. Handler yields Resume(k, v) again
2. VM calls pyk.take() → returns None
3. Raise "continuation already consumed"
```

No Arc. No AtomicBool. No shared flag. Just Option::take returning None.

#### Pass (reperform)

```
1. Handler yields Pass()
2. VM calls pyk.take() → returns Some(continuation)
3. Append current handler fiber to continuation
4. Detach current handler from parent
5. Switch to parent
6. Re-perform effect at parent handler
```

```
  Handler yields Pass():

  pyk.take() → Some([F3, F2])

  Append F1 to continuation → [F3, F2, F1]
  Detach F1 from F0
  Switch to F0's handler (or next handler up the chain)

       continuation = [F3, F2, F1]     F0
                                       ▲ re-perform here
```

#### Handler throws without Resume

```
1. Handler's generator raises exception
2. Python unwinds handler frame → PyK still alive (in generator locals)
3. VM catches exception, reads boundary fiber's effect context
4. VM checks: was continuation consumed? → read PyK.is_consumed()
5. If not consumed: free orphaned fibers via PyK.take()
6. Propagate exception to parent
```

The boundary fiber's stored effect context is the backup for cleanup.
PyK is accessible because the handler's generator frame hasn't been GC'd yet
(the VM holds a reference to the generator).

## What clone_handle() Is Replaced By

Every current use of `clone_handle()` maps to one of:

| Current pattern | Replacement |
|---|---|
| `ProgramDispatch.origin = k.clone_handle()` | Don't store. Walk chain or read boundary fiber. |
| `ProgramDispatch.handler_continuation = k.clone_handle()` | Don't store. Read from PyK on demand. |
| `boundary.pending_continuation = k.clone_handle()` | Store `Option<*const PyK>` (weak ref) or just the fiber head ID for cleanup. |
| `DoCtrl::Resume { continuation: k.clone_handle() }` | `DoCtrl::Resume` takes ownership (move). |
| OwnedControlContinuation::Started(k.clone_handle()) | Move, not clone. |

## Migration Steps

Each step must pass the full test suite with zero regressions.

### Step 1: Remove ProgramDispatch.origin and ProgramDispatch.handler_continuation

These are the two biggest consumers of clone_handle(). Replace their uses with:
- Traceback: walk fiber chain to find dispatch context
- Error cleanup: read from boundary fiber or PyK

### Step 2: Remove clone_handle() method

Make every remaining caller use move semantics or read from PyK.
Continuation stops implementing any form of Clone.

### Step 3: Replace consumed tracking with Option::take

Remove `consumed: bool` (or `Arc<AtomicBool>`). PyK uses `Option<Continuation>`.
One-shot is `take()` returning `None`.

### Step 4: Remove ContId

With single ownership, there's no need for identity tracking.
Continuation = { fibers, last_fiber }. Two fields.

## Invariants

After this spec is implemented:

1. **At any point in time, a continuation exists in exactly ONE place.**
   Either in PyK, or being moved through a DoCtrl variant, or being
   reattached by the VM. Never in two places simultaneously.

2. **clone_handle() does not exist.** There is no way to create a second
   reference to the same continuation.

3. **One-shot is Option::take().** No flags, no Arc, no shared state.

4. **The VM does not store continuations.** ProgramDispatch either doesn't
   exist or stores display-only data (effect repr string, handler names).
   It does NOT store Continuation objects.

5. **Error cleanup reads from PyK or boundary fiber.** The handler's
   generator frame holds PyK. The boundary fiber holds the effect context.
   Both are reachable without storing a continuation copy.
