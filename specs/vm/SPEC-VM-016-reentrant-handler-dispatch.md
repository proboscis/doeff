# SPEC-VM-016: Re-entrant Handler Dispatch (Koka Semantics)

## Status: Draft (Revision 1)

### Revision 1 Changelog

Initial draft. Defines re-entrant handler semantics replacing the "busy boundary" model.

| Tag | Section | Change |
|-----|---------|--------|
| **R1-A** | Handler visibility | **Handlers are re-entrant by default.** No busy exclusion. `visible_handlers()` returns the full scope chain without filtering. Effects yielded during handler execution dispatch through the normal handler chain INCLUDING the currently-handling handler. |
| **R1-B** | Mask primitive | **`Mask(effect_types, body)` is an explicit DoCtrl.** Programmer-controlled opt-in to skip the innermost handler for specified effect types. Replaces the implicit always-on busy exclusion. |
| **R1-C** | Override pattern | **`override` handler pattern uses mask semantics.** A handler that wants to replace an outer handler for the same effect type can use `Mask` to skip itself when re-performing, preventing infinite recursion. |
| **R1-D** | Dispatch machinery cleanup | **`completed`, `lazy_pop_completed`, `consumed_cont_ids` simplified.** The busy set computation is eliminated. `DispatchContext` no longer needs `handler_chain` or `handler_idx` for visibility filtering. |

---

## 1. Summary

This spec replaces the doeff VM's "busy boundary" handler visibility model with **re-entrant handler dispatch**, following Koka's algebraic effect semantics (Koka book §3.4.7).

**Current model (WRONG):** When a handler is executing, it is marked "busy" and excluded from `visible_handlers()`. Effects yielded inside a handler body cannot reach the handler itself. This is an implicit, always-on mask that users cannot control.

**Target model (THIS SPEC):** Handlers are re-entrant by default. An effect yielded during handler execution dispatches through the full handler chain, including the currently-handling handler. The programmer explicitly controls handler skipping via `Mask(effect_types, body)` when needed.

```
Current (busy boundary):           Target (re-entrant):
                                   
  yield Perform(E)                   yield Perform(E)
    │                                  │
    ▼                                  ▼
  visible_handlers():                visible_handlers():
    scope_chain MINUS busy             scope_chain (unchanged)
    │                                  │
    ▼                                  ▼
  handler H is excluded              handler H is included
  (implicit mask)                    (re-entrant by default)
                                   
                                   To skip H explicitly:
                                     Mask([E], body)
                                       yield Perform(E)
                                         → skips innermost H for E
```

### 1.1 Relationship to Existing Specs

- **SPEC-008 (Rust VM)**: INV-8 "Busy Boundary (Top-Only)" is **superseded** by this spec. `visible_handlers()` pseudocode in §dispatch is revised. `start_dispatch` no longer references busy exclusion.
- **SPEC-VM-010 (Non-terminal Delegate)**: Scope behavior section revised — handler body effects dispatch WITH the current handler, not without.
- **SPEC-TYPES-001 (Program/Effect Separation)**: References to "busy boundary" removed. `Eval` justification revised.
- **SPEC-VM-015 (Segment-Owned State)**: Unaffected. Segment ownership is orthogonal to handler visibility policy.
- **VM-FRAME-001 (Typed Frames)**: Orthogonal. Frame typing does not depend on visibility semantics.

### 1.2 Theoretical Basis

In Koka's effect system (Leijen 2017, Koka book §3.4.7):

1. **Deep handlers** are the default: when a handled computation resumes, the resumed computation runs under the same handler. This means the handler is naturally re-entrant — a resumed computation can yield the same effect again and it will be handled by the same handler.

2. **`mask<eff>`** is an explicit primitive that masks (hides) the innermost handler for effect `eff` within its body. The effect passes through to the next handler in the chain. This is opt-in, not default.

3. **`override`** is sugar for installing a handler and then masking the effect behind it (`mask behind<eff>`), so the new handler replaces the previous one.

The doeff VM's current "busy boundary" implements the opposite: an implicit, always-on mask that cannot be disabled. This prevents legitimate use cases like:
- A logging handler that logs its own internal operations
- A state handler that reads its own state during state transitions
- An error handler that performs error-producing operations during recovery

---

## 2. Handler Visibility: Re-entrant by Default

### 2.1 `visible_handlers()` — Trivial Implementation

```rust
/// Compute visible handlers.
///
/// Handlers are re-entrant by default. No busy exclusion.
/// The full scope_chain is returned unchanged.
///
/// Mask semantics (SPEC-VM-016 §3) are handled at the Mask DoCtrl
/// level, not in visible_handlers().
fn visible_handlers(&self, scope_chain: &[Marker]) -> Vec<Marker> {
    scope_chain.to_vec()
}
```

The entire busy-set computation is eliminated:
- No `dispatch_stack.last()` check
- No `top.completed` guard
- No `HashSet<Marker>` busy set construction
- No filtering of scope_chain

### 2.2 `start_dispatch()` — Simplified

```rust
fn start_dispatch(&mut self, py: Python<'_>, effect: Py<PyAny>) -> Result<StepEvent, VMError> {
    self.lazy_pop_completed();
    let scope_chain = self.current_scope_chain();
    
    // No busy filtering — full scope_chain is visible
    let handler_chain = scope_chain.to_vec();
    
    if handler_chain.is_empty() {
        return Err(VMError::unhandled_effect_opaque());
    }
    
    // ... find_matching_handler, capture continuation, push dispatch context ...
}
```

### 2.3 `DispatchContext` — Reduced Fields

The `handler_chain` and `handler_idx` fields on `DispatchContext` were primarily needed for busy-set computation. With re-entrant dispatch:

- `handler_chain` may still be useful for `Delegate` (re-perform from handler_idx+1), so it stays
- `handler_idx` stays (needed for Delegate advancement)
- The busy-set computation path is removed

```rust
struct DispatchContext {
    dispatch_id: DispatchId,
    effect: Py<PyAny>,
    handler_chain: Vec<Marker>,   // retained for Delegate
    handler_idx: usize,           // retained for Delegate
    k_user: Continuation,
    prompt_seg_id: SegmentId,
    completed: bool,              // retained for dispatch lifecycle
}
```

### 2.4 Semantic Consequence: Self-Dispatch

With re-entrant handlers, an effect yielded inside a handler body CAN match the same handler:

```python
def state_handler(effect, k):
    if isinstance(effect, GetEffect):
        # This yield Perform(Get(...)) will dispatch through
        # the full handler chain, including this handler itself.
        # If this handler handles Get, it will recurse.
        current = yield Get("some-key")  # re-entrant!
        return (yield Resume(k, current))
    yield Delegate()
```

This is **correct and intentional**. If the programmer wants to avoid self-dispatch, they use `Mask`:

```python
def state_handler(effect, k):
    if isinstance(effect, GetEffect):
        # Mask Get effects in this body — they skip this handler
        masked = Mask([GetEffect], Get("some-key"))
        current = yield masked
        return (yield Resume(k, current))
    yield Delegate()
```

---

## 3. Mask: Explicit Handler Skipping

### 3.1 `Mask` DoCtrl Definition

```rust
/// Mask(effect_types, body) — evaluate body with the innermost handler
/// for each specified effect type skipped.
///
/// Koka equivalent: mask<eff> { body }
///
/// When body yields an effect E where type(E) ∈ effect_types,
/// dispatch skips the innermost handler that handles E and
/// dispatches to the next matching handler in the chain.
Mask {
    /// Python list of effect type classes to mask
    effect_types: Py<PyList>,
    /// DoExpr body to evaluate under the mask
    body: Py<PyAny>,  // DoExpr
}
```

### 3.2 Mask Dispatch Semantics

When the VM processes `Mask(effect_types, body)`:

1. Push a **mask frame** onto the current segment's mask stack (or equivalent mechanism)
2. The mask frame records: `{ effect_types: Set<PyType>, source_handler: Marker }`
   - `source_handler` is the marker of the handler whose body we are executing in (if any)
3. Evaluate `body` normally
4. During dispatch of any effect E within `body`:
   - If `type(E) ∈ effect_types` AND the first matching handler is `source_handler`:
     - Skip `source_handler`, dispatch to the next matching handler
   - Otherwise: dispatch normally (re-entrant)
5. When `body` completes, pop the mask frame

### 3.3 Mask Stack

```rust
/// Per-segment mask stack.
/// Active masks are consulted during dispatch to skip specified handlers.
struct MaskEntry {
    /// Effect types to mask (Python type objects)
    effect_types: Vec<Py<PyType>>,
    /// Handler marker to skip for these effect types
    skip_handler: Marker,
}
```

The mask stack is segment-local (per SPEC-VM-015's segment-owned state principle). When dispatch searches for a handler:

```rust
fn find_matching_handler_with_mask(
    &self,
    py: Python<'_>,
    handler_chain: &[Marker],
    effect: &Bound<'_, PyAny>,
) -> Result<(usize, Marker, HandlerEntry), VMError> {
    let masks = self.current_segment_masks();
    
    for (idx, &marker) in handler_chain.iter().enumerate() {
        // Check if this handler is masked for this effect type
        if self.is_masked(py, &masks, marker, effect) {
            continue;  // skip masked handler
        }
        
        if let Some(entry) = self.handlers.get(&marker) {
            if entry.handler.can_handle(effect) {
                return Ok((idx, marker, entry.clone()));
            }
        }
    }
    Err(VMError::UnhandledEffect(effect.clone()))
}

fn is_masked(
    &self,
    py: Python<'_>,
    masks: &[MaskEntry],
    handler_marker: Marker,
    effect: &Bound<'_, PyAny>,
) -> bool {
    for mask in masks {
        if mask.skip_handler == handler_marker {
            for effect_type in &mask.effect_types {
                if effect.is_instance(effect_type.bind(py)).unwrap_or(false) {
                    return true;
                }
            }
        }
    }
    false
}
```

### 3.4 Python Surface API

```python
from doeff import Mask

# Mask specific effects in a body
masked_body = Mask([GetEffect, PutEffect], some_program())

# In a handler — skip self for specific effects
def logging_handler(effect, k):
    if isinstance(effect, LogEffect):
        # Log to our own log (re-entrant — handled by this handler)
        yield Tell(f"Handling: {effect}")
        
        # But read config without re-entering (masked)
        config = yield Mask([GetEffect], Get("log-config"))
        
        return (yield Resume(k, None))
    yield Delegate()
```

### 3.5 Interaction with Delegate/Pass

`Mask` is orthogonal to `Delegate` and `Pass`:

- **`Delegate()`** — non-terminal re-perform from `handler_idx + 1`. Already skips the current handler by index advancement. Mask does not affect Delegate dispatch.
- **`Pass(effect?)`** — terminal pass-through. Already uses `handler_idx + 1`. Mask does not affect Pass dispatch.
- **`Mask(types, body)` + `yield Perform(E)`** — the effect dispatches through the full chain, but the innermost handler for `E` matching the mask's `skip_handler` is skipped.

The key difference: Delegate/Pass skip by **position** (index in dispatch chain). Mask skips by **effect type** (any dispatch, not just the current one).

---

## 4. Override Handler Pattern

### 4.1 Concept

An "override" handler replaces an outer handler for the same effect type. In Koka:

```koka
fun override handler(action) {
    with handler { ... }      // install new handler
    mask behind<eff>(action)  // mask the NEW handler for eff in action
}
```

In doeff, the equivalent pattern:

```python
def override_state_handler(effect, k):
    """Handles Get/Put but delegates to outer handler for internal state access."""
    if isinstance(effect, GetEffect):
        # Use Mask to read from the OUTER state handler
        outer_value = yield Mask([GetEffect], Get(effect.key))
        transformed = transform(outer_value)
        return (yield Resume(k, transformed))
    yield Delegate()
```

### 4.2 No Special `override` Keyword

Unlike Koka, doeff does not need an `override` keyword. The `Mask` primitive is sufficient — users compose it manually. If an `override` convenience is desired, it can be added as a Python-side wrapper without VM changes:

```python
def override_handler(handler_fn, *effect_types):
    """Wrap a handler so it masks itself for specified effect types."""
    def wrapped(effect, k):
        # ... applies Mask automatically around handler body ...
        pass
    return wrapped
```

---

## 5. Removed Machinery

### 5.1 Busy Set Computation

**Deleted from `visible_handlers()`:**
```rust
// REMOVED: busy set computation
let busy: HashSet<Marker> = top.handler_chain[..=top.handler_idx]
    .iter()
    .copied()
    .collect();
scope_chain.iter().filter(|m| !busy.contains(m)).collect()
```

### 5.2 Top-Only Busy Boundary Check

**Deleted from `visible_handlers()`:**
```rust
// REMOVED: top dispatch check
let Some(top) = self.dispatch_stack.last() else {
    return scope_chain.to_vec();
};
if top.completed {
    return scope_chain.to_vec();
}
```

### 5.3 INV-8 (SPEC-008)

INV-8 "Busy Boundary (Top-Only)" is **superseded** by this spec. The invariant is replaced by:

**INV-8 (Revised): Re-entrant Handler Dispatch**
```
Handlers are re-entrant by default. visible_handlers() returns the
full scope_chain without filtering. Effects yielded during handler
execution dispatch through the complete handler chain, including the
currently-handling handler.

Handler skipping is opt-in via Mask(effect_types, body) DoCtrl.
Mask records are segment-local and consulted during dispatch to skip
the specified handler for the specified effect types.

See SPEC-VM-016 for semantics.
```

---

## 6. Interaction with Existing Dispatch Features

### 6.1 Deep Handlers (Default)

doeff handlers are deep by default: when a continuation is resumed, the resumed computation runs under the same handler scope (the handler is reinstalled around the continuation). This is already correct and unchanged by this spec.

Re-entrant dispatch + deep handlers means: a resumed computation that yields the same effect will be handled by the same handler instance. This is the Koka default behavior.

### 6.2 Interceptors (`WithIntercept`)

Interceptors observe effects before handler dispatch. Re-entrant dispatch does not change interceptor semantics — interceptors still see effects in installation order, and their `Mask` status is orthogonal.

### 6.3 Scheduler Handler (`SPEC-SCHED-001`)

The cooperative scheduler handler handles `Spawn`, `Gather`, `Race`, etc. With re-entrant dispatch, a spawned task that yields `Spawn` will be handled by the same scheduler handler. This is correct — the scheduler should manage its own spawned tasks.

### 6.4 `Eval(expr, handlers)`

`Eval` creates a fresh scope with explicit handlers. `Eval` does not interact with the mask stack (masks are segment-local, and `Eval` creates a new segment). `Eval`'s justification no longer includes "busy boundary avoidance" — its purpose is scoped handler installation.

---

## 7. Migration Path

### 7.1 Behavioral Change

This is a **semantic change** — programs that relied on implicit busy exclusion may behave differently:

| Scenario | Before (busy) | After (re-entrant) |
|----------|---------------|---------------------|
| Handler yields same effect type | Dispatches to outer handler | Dispatches to SAME handler (recursive) |
| Handler yields different effect type | Dispatches normally | Dispatches normally (no change) |
| Handler yields Delegate() | Skips current handler | Skips current handler (no change) |
| Handler yields Pass(effect) | Skips current handler | Skips current handler (no change) |

### 7.2 Breaking Case: Accidental Recursion

Handlers that yield effects of their own type without explicit delegation will now recurse instead of forwarding to an outer handler. This is the correct semantic, but existing handlers may need `Mask` or `Delegate` to preserve behavior:

```python
# BEFORE: implicitly forwarded to outer handler (busy exclusion)
def handler(effect, k):
    if isinstance(effect, MyEffect):
        result = yield MyEffect("inner")  # went to outer handler
        return (yield Resume(k, result))

# AFTER: must explicitly delegate if outer forwarding was intended
def handler(effect, k):
    if isinstance(effect, MyEffect):
        result = yield Delegate()  # explicit forward to outer
        return (yield Resume(k, result))
```

### 7.3 Migration Checklist

1. Audit all handler implementations for self-effect yields
2. Add `Mask` or `Delegate` where outer forwarding was intended
3. Test handler composition with nested `WithHandler` stacks

---

## 8. Acceptance Criteria

### Core Semantics
- [ ] `visible_handlers()` returns `scope_chain` unchanged (no busy filtering)
- [ ] Busy set computation removed from `visible_handlers()`
- [ ] Effects yielded during handler execution can match the executing handler
- [ ] `DispatchContext` retains `handler_chain` and `handler_idx` for Delegate

### Mask Primitive
- [ ] `Mask` DoCtrl variant added to Rust `DoCtrl` enum
- [ ] `Mask` Python class added as DoCtrl surface API
- [ ] Mask stack is segment-local (per SPEC-VM-015)
- [ ] Dispatch consults mask stack to skip specified handlers
- [ ] Mask frame pushed before body evaluation, popped after completion
- [ ] Nested masks compose correctly (multiple active masks)

### Spec Updates
- [ ] SPEC-008 INV-8 revised to reference SPEC-VM-016
- [ ] SPEC-008 `visible_handlers()` pseudocode updated
- [ ] SPEC-008 `start_dispatch` pseudocode updated
- [ ] SPEC-VM-010 §Scope behavior revised (re-entrant, not busy-excluded)
- [ ] SPEC-TYPES-001 "busy boundary" references removed

### Testing
- [ ] Test: handler yields same effect type → handled by same handler (re-entrant)
- [ ] Test: `Mask([EffType], body)` → effect dispatches to outer handler
- [ ] Test: nested Mask → each mask independently effective
- [ ] Test: Mask + Delegate → Delegate unaffected by Mask
- [ ] Test: 3-level handler nesting → all handlers visible at all levels
- [ ] Test: handler Resume → resumed computation sees same handler (deep handler)
- [ ] `cargo test --manifest-path packages/doeff-vm/Cargo.toml` — ALL pass
- [ ] `uv run pytest -q` — ALL pass

---

## 9. Open Questions

1. **Mask lifetime**: Should Mask be scoped to a DoExpr body (as specified), or should there be a "mask until end of handler" variant? Koka only has body-scoped mask.

2. **Mask serialization**: If we add VM state serialization (for durable execution / SPEC-CORE-425), mask entries need to be serializable. Effect types are Python type objects — how to serialize?

3. **Performance**: The busy-set computation was O(n) per dispatch where n = handler_chain length. The mask check is O(m × t) where m = active masks and t = effect types per mask. For typical programs, m is very small (0-2). Is this acceptable?

---

## 10. References

- Koka book §3.4.7 "Masking Effects" — https://koka-lang.github.io/koka/doc/book.html
- Leijen, D. (2017). "Type directed compilation of row-typed algebraic effects"
- SPEC-008: Rust VM (§dispatch, §INV-8)
- SPEC-VM-010: Non-terminal Delegate
- SPEC-TYPES-001: Program/Effect Separation
- SPEC-VM-015: Segment-Owned Execution State
