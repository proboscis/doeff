# SPEC-VM-010: Non-Terminal Delegate (Re-Perform Semantics)

## Status: Draft

## Summary

Redesign `Delegate` to match Koka/OCaml 5 re-perform semantics: a handler that delegates an effect to the next handler in the chain **receives the result back** and can transform it before resuming the original continuation.

Introduces `Pass` as the new name for the current terminal pass-through behavior.

## Motivation

doeff's current `Delegate` is terminal — the handler gives up control and never receives the delegated result. This diverges from algebraic effects literature where "delegation" (re-performing) is always non-terminal.

### The problem: handler interception is impossible

```python
# GOAL: intercept Spawn, coerce the task handle, resume user with coerced handle
def spawn_intercept(effect, k):
    if isinstance(effect, SpawnEffect):
        raw = yield Delegate()                    # delegate to scheduler
        return (yield Resume(k, coerce(raw)))     # resume user with coerced handle
    yield Pass()                                  # other effects: transparent
```

Today this is impossible because:
1. `Delegate` is terminal — `raw = yield Delegate()` never receives a value
2. The outer handler (scheduler) receives **k_user directly** and resumes it, bypassing the intercepting handler entirely

### Koka/OCaml 5 reference

In both systems, re-performing inside a handler clause is non-terminal. The handler always gets the result back.

```koka
handler {
  ctl ask() {
    val raw = ask()        // re-perform → outer handler handles, result comes back
    resume(raw * 2)        // inner handler transforms and resumes user
  }
}
```

Key mechanism: the outer handler receives a **synthetic continuation** (K_new) that captures the inner handler's remaining code. Resuming K_new sends the value back to the inner handler, not to the original user continuation.

## Design

### Two distinct operations

| Operation | Semantics | Koka equivalent | Terminal? |
|-----------|-----------|-----------------|-----------|
| `Pass()` | "I don't handle this effect. Skip me entirely." | No clause for this effect (automatic pass-through) | Yes |
| `Delegate()` | "Re-perform this effect to the next handler. Give me the result." | `val r = effect()` inside `ctl` clause | **No** |

### Python API

```python
import doeff_vm

# Pass: transparent pass-through (current Delegate behavior, renamed)
yield doeff_vm.Pass()

# Delegate: non-terminal re-perform (new behavior)
raw = yield doeff_vm.Delegate()
# Delegate(effect=...) to re-perform a DIFFERENT effect:
raw = yield doeff_vm.Delegate(effect=SomeOtherEffect(...))
```

### Backward compatibility

| Current code | Migration |
|---|---|
| `yield Delegate()` (without return) | → `yield Pass()` |
| `return (yield Delegate())` (with return) | → `return (yield Delegate())` (now works correctly) |

Sites requiring migration (terminal pass-through pattern):
- `doeff/effects/future.py`: `sync_await_handler`, `async_await_handler` (2 sites)
- `doeff/effects/scheduler_internal.py`: `sync_external_wait_handler`, `async_external_wait_handler` (2 sites)

Sites already compatible (return pattern):
- `doeff/effects/writer.py`: `handle_listen_tell` (2 sites)
- `doeff/effects/intercept.py`: `handle_intercept` (1 site)

## Semantics

### Core invariant: K_new continuation swap

When handler A yields `Delegate()`:

1. VM captures A's remaining generator state as **K_new**
2. VM updates `DispatchContext.k_user = K_new`
3. Next handler B receives **(effect, K_new)** — not the original k_user
4. **A retains k_user** (received as handler argument)

When B does `Resume(K_new, value)`:
- Value flows to A's generator (not to the original user code)
- A can inspect, transform, and then `Resume(k_user, transformed_value)`

### Flow diagram

```
user code             A (inner handler)           B (outer handler)
─────────             ──────────────────           ──────────────────
yield Ask() ────→   A receives (effect, k_user)
                    A retains k_user

                    yield Delegate()
                      VM: K_new = capture(A's frames)
                           clear A's segment frames
                           DispatchContext.k_user = K_new
                                            ────→  B receives (effect, K_new)
                                                   B does not know about k_user

                                                   yield Resume(K_new, 42)
                    ←────────────────────────────
                    raw = 42
                    yield Resume(k_user, raw * 2)
←─────────────────
x = 84
return x + 1 = 85
```

### Comparison: Pass vs Delegate

```
Pass (terminal):                        Delegate (non-terminal):
─────────────────                       ────────────────────────
A.frames CLEARED                        A.frames CAPTURED as K_new
DispatchContext.k_user unchanged         DispatchContext.k_user = K_new
B receives k_user                       B receives K_new
B resumes k_user → user directly        B resumes K_new → value goes to A
A is not involved                       A transforms, resumes k_user
```

### Handler responsibility

| Actor | Responsibility |
|-------|---------------|
| A (inner, delegating handler) | Holds k_user. Decides what value the user receives. Calls `Resume(k_user, ...)`. |
| B (outer, handling handler) | Receives K_new (opaque). Resumes K_new with the handled value. Does not know about k_user. |
| VM | Creates K_new from A's state. Swaps DispatchContext.k_user. Routes values correctly. |

### Scope behavior (already correct)

The VM's `visible_handlers()` already excludes the current handler via busy-marker filtering:

```rust
// vm.rs visible_handlers()
let busy: HashSet<Marker> = top.handler_chain[..=top.handler_idx]
    .iter().copied().collect();
scope_chain.iter().filter(|m| !busy.contains(m)).collect()
```

This means:
- `yield effect` inside a handler body → dispatches **without** the current handler (Koka prompt-level equivalent)
- `yield Delegate()` → uses DispatchContext.handler_chain with handler_idx+1 (explicit skip)

Both mechanisms are already in place. No scope changes needed.

## VM Implementation

### New DoCtrl variant

```rust
// do_ctrl.rs
pub enum DoCtrl {
    // ... existing variants ...
    Pass {
        effect: DispatchEffect,
    },
    // Delegate variant: unchanged structurally, but semantics change
    Delegate {
        effect: DispatchEffect,
    },
}
```

`Delegate` retains its name and structure. `Pass` is the new variant for the old terminal behavior.

### New DoExprTag

```rust
// pyvm.rs
pub enum DoExprTag {
    // ... existing ...
    Delegate = 8,       // semantics change: now non-terminal
    Pass = 19,          // new: terminal pass-through (old Delegate behavior)
}
```

### handle_delegate (non-terminal — NEW semantics)

```rust
fn handle_delegate(&mut self, effect: DispatchEffect) -> StepEvent {
    let (handler_chain, start_idx, from_idx, dispatch_id) = /* from dispatch_stack */;

    let inner_seg_id = self.current_segment;

    // 1. Capture A's remaining state as K_new
    let k_new = self.capture_continuation(Some(dispatch_id))
        .expect("delegate requires current segment");

    // 2. Clear A's segment frames (prevents double-reference to generator)
    if let Some(seg) = self.current_segment_mut() {
        seg.frames.clear();
    }

    // 3. Swap k_user → K_new in DispatchContext
    {
        let top = self.dispatch_stack.last_mut().unwrap();
        top.k_user = k_new.clone();
    }

    // 4. Find next matching handler and invoke (same as before)
    for idx in start_idx..handler_chain.len() {
        let marker = handler_chain[idx];
        if let Some(entry) = self.handlers.get(&marker) {
            if entry.handler.can_handle(&effect) {
                let handler = entry.handler.clone();

                let k_user = {
                    let top = self.dispatch_stack.last_mut().unwrap();
                    top.handler_idx = idx;
                    top.effect = effect.clone();
                    top.k_user.clone()  // This is now K_new
                };

                let scope_chain = self.current_scope_chain();
                let handler_seg = Segment::new(marker, inner_seg_id, scope_chain);
                let handler_seg_id = self.alloc_segment(handler_seg);
                self.current_segment = Some(handler_seg_id);

                if handler.py_identity().is_some() {
                    self.register_continuation(k_user.clone());
                }
                let ir_node = handler.invoke(effect.clone(), k_user);
                return self.evaluate(ir_node);
            }
        }
    }

    StepEvent::Error(VMError::delegate_no_outer_handler(effect))
}
```

### handle_pass (terminal — OLD Delegate semantics)

```rust
fn handle_pass(&mut self, effect: DispatchEffect) -> StepEvent {
    // Identical to current handle_delegate implementation
    // frames.clear(), no K_new capture, k_user unchanged
    // ...
}
```

### step_handle_yield routing

```rust
DoCtrl::Delegate { effect } => self.handle_delegate(effect),  // non-terminal (new)
DoCtrl::Pass { effect } => self.handle_pass(effect),          // terminal (old)
```

### is_terminal classification

```rust
// In apply_stream_step:
let is_terminal = matches!(
    &yielded,
    DoCtrl::Resume { .. }
        | DoCtrl::Transfer { .. }
        | DoCtrl::TransferThrow { .. }
        | DoCtrl::Pass { .. }       // Pass is terminal (was Delegate)
    // Note: Delegate is NOT listed — it is non-terminal
    // (Though Python generators go through receive_python_result, not this path)
);
```

### Python class

```rust
#[pyclass(name = "Pass", extends=PyDoCtrlBase)]
pub struct PyPass {
    #[pyo3(get)]
    pub effect: Option<Py<PyAny>>,
}

#[pymethods]
impl PyPass {
    #[new]
    #[pyo3(signature = (effect=None))]
    fn new(py: Python<'_>, effect: Option<Py<PyAny>>) -> PyResult<PyClassInitializer<Self>> {
        // Same validation as current Delegate
        Ok(PyClassInitializer::from(PyDoExprBase)
            .add_subclass(PyDoCtrlBase { tag: DoExprTag::Pass as u8 })
            .add_subclass(PyPass { effect }))
    }
}
```

### Python exports

```python
# rust_vm.py
pass_ = vm.Pass          # 'pass' is a keyword, use pass_
delegate = vm.Delegate    # semantics changed: now non-terminal

# __init__.py
"Pass",
"Delegate",
```

## Correctness Proof: handle_handler_return

When B (outer handler) returns a value, `handle_handler_return` checks dispatch completion:

```rust
if let Some(caller_id) = seg.caller {
    if caller_id == top.prompt_seg_id {
        top.completed = true;
    }
}
```

**B returns (after resuming K_new):**
- B's handler_seg.caller = inner_seg (A's segment)
- inner_seg ≠ prompt_seg_id → **dispatch NOT completed** ✓
- Value flows to A's segment

**A returns (after resuming k_user):**
- A was resumed via K_new in an exec_seg
- The resume result eventually flows back through to inner_seg
- inner_seg.caller = prompt_seg_id → **dispatch completed** ✓
- Value delivered to user

## Edge Cases

### B does Pass instead of Resume

If B passes to C, C gets K_new (already in DispatchContext). C's Resume(K_new, value) goes to A. Correct.

### B does Delegate (nested non-terminal)

B delegates to C. VM creates K_new2 from B's state. C gets K_new2. C's Resume sends value to B. B transforms, resumes K_new. Value goes to A. A transforms, resumes k_user. Correct recursion.

### B does Transfer(K_new, value)

Transfer is one-shot tail-call. Value goes to A's generator (via K_new). A continues with the value. A can Resume(k_user, ...). Correct.

### No outer handler found

`VMError::delegate_no_outer_handler(effect)` — same as current behavior.

### A yields Delegate but doesn't Resume k_user

A returns a value without resuming k_user. The value goes through handle_handler_return. k_user is NOT consumed (no one resumed it). dispatch is marked completed. k_user.cont_id is inserted into consumed_cont_ids. The continuation is leaked but not double-resumed. This is valid (handler decided not to resume — same as a handler that returns without Resume today).

## Migration Plan

### Phase 1: Add Pass, keep Delegate terminal (backward compat)

1. Add `Pass` variant to DoCtrl, DoExprTag, PyClass
2. Add `handle_pass` (copy of current `handle_delegate`)
3. Route `DoCtrl::Pass` → `handle_pass`
4. Export `Pass` from Python
5. All existing code continues to work (Delegate unchanged)

### Phase 2: Migrate existing Delegate call sites to Pass

6. `doeff/effects/future.py`: `yield doeff_vm.Delegate()` → `yield doeff_vm.Pass()`
7. `doeff/effects/scheduler_internal.py`: same migration
8. `doeff/effects/writer.py`: uses `return (yield Delegate())` — keep as Delegate
9. `doeff/effects/intercept.py`: uses `return (yield Delegate())` — keep as Delegate
10. Update tests referencing Delegate pass-through behavior

### Phase 3: Make Delegate non-terminal

11. Implement K_new capture in `handle_delegate`
12. Update `DispatchContext.k_user` swap logic
13. Update `is_terminal` classification
14. Add tests for non-terminal Delegate flow
15. Add tests for nested Delegate (A delegates → B delegates → C)

### Phase 4: Enable Spawn interception

16. Implement spawn intercept handler using non-terminal Delegate
17. Remove `GetHandlers + CreateContinuation` workaround from scheduler
18. Add `CreateContinuationInDispatchScope` (optional simplification)

## Files Changed

| File | Change | Phase |
|------|--------|-------|
| `packages/doeff-vm/src/do_ctrl.rs` | Add `Pass` variant | 1 |
| `packages/doeff-vm/src/pyvm.rs` | `DoExprTag::Pass`, `PyPass` class, `classify_yielded` arm, module export | 1 |
| `packages/doeff-vm/src/vm.rs` | `handle_pass`, routing in `step_handle_yield` | 1 |
| `doeff/rust_vm.py` | Export `pass_` alias | 1 |
| `doeff/__init__.py` | Export `Pass` | 1 |
| `doeff/effects/future.py` | `Delegate()` → `Pass()` (2 sites) | 2 |
| `doeff/effects/scheduler_internal.py` | `Delegate()` → `Pass()` (2 sites) | 2 |
| `tests/` | Update Delegate/Pass references | 2 |
| `packages/doeff-vm/src/vm.rs` | K_new capture, DispatchContext swap in `handle_delegate` | 3 |
| `packages/doeff-vm/src/vm.rs` | `is_terminal` update | 3 |
| `tests/` | Non-terminal Delegate tests | 3 |
| `doeff/effects/spawn.py` | Spawn intercept handler | 4 |
| `packages/doeff-vm/src/scheduler.rs` | Remove GetHandlers+CreateContinuation workaround | 4 |

## References

- Koka handler semantics: `lib/std/core/hnd.kk` (yield-to/yield-prompt mechanism)
- OCaml 5 effect handlers: `stdlib/effect.mli` (perform/reperform/continue)
- SPEC-008-rust-vm.md: Rust VM architecture
- SPEC-SCHED-001: Cooperative scheduling (Spawn/Wait/Gather/Race)
- SPEC-EFF-004: Control effects (current Delegate documentation)
