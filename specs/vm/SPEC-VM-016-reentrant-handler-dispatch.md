# SPEC-VM-016: Re-entrant Handler Dispatch (Koka Semantics)

## Status: Draft (Revision 2)

### Revision 1 Changelog

Initial draft. Defines re-entrant handler semantics replacing the "busy boundary" model.

| Tag | Section | Change |
|-----|---------|--------|
| **R1-A** | Handler visibility | **Handlers are re-entrant by default.** No busy exclusion. `visible_handlers()` returns the full scope chain without filtering. Effects yielded during handler execution dispatch through the normal handler chain INCLUDING the currently-handling handler. |
| **R1-B** | Mask primitive | **`Mask(effect_types, body)` is an explicit DoCtrl.** Programmer-controlled opt-in to skip the innermost handler for specified effect types. Replaces the implicit always-on busy exclusion. |
| **R1-C** | Override pattern | **`override` handler pattern uses mask semantics.** A handler that wants to replace an outer handler for the same effect type can use `Mask` to skip itself when re-performing, preventing infinite recursion. |
| **R1-D** | Dispatch machinery cleanup | **`completed`, `lazy_pop_completed`, `consumed_cont_ids` simplified.** The busy set computation is eliminated. `DispatchContext` no longer needs `handler_chain` or `handler_idx` for visibility filtering. |

### Revision 2 Changelog

Aligns mask model with SPEC-008 R17 segment architecture. Introduces MaskBoundary segments and MaskBehind. Supersedes WithIntercept.

| Tag | Section | Change |
|-----|---------|--------|
| **R2-A** | §3 Mask | **MaskBoundary segments.** Mask is now a `SegmentKind` (`MaskBoundary { masked_effects, behind }`) instead of a per-segment mask stack. Created by `Mask`/`MaskBehind` DoCtrl. Handler walk skips `PromptBoundary` segments per `MaskBoundary` rules. |
| **R2-B** | §3, §4 | **MaskBehind.** Separate DoCtrl for the override pattern. `MaskBehind(effect_types, body)` creates `MaskBoundary` with `behind=true`. Skips the handler BEHIND the override handler — i.e., the outer handler that the override replaces. |
| **R2-C** | §4.3, §6.2 | **WithIntercept superseded.** Override handler pattern (handler + `MaskBehind`) replaces `WithIntercept` for all use cases. See SPEC-WITH-INTERCEPT deprecation notice. |
| **R2-D** | §2 | **Handler-on-segment alignment.** References updated to match SPEC-008 R17 architecture (no `VM.handlers`, no `scope_chain`, handler walk via caller chain). |

---

## 1. Summary

This spec replaces the doeff VM's "busy boundary" handler visibility model with **re-entrant handler dispatch**, following Koka's algebraic effect semantics (Koka book §3.4.7).

**Current model (WRONG):** When a handler is executing, it is marked "busy" and excluded from `visible_handlers()`. Effects yielded inside a handler body cannot reach the handler itself. This is an implicit, always-on mask that users cannot control.

**Target model (THIS SPEC):** Handlers are re-entrant by default. An effect yielded during handler execution dispatches through the full caller chain, including the currently-handling handler's `PromptBoundary` segment. The programmer explicitly controls handler skipping via `Mask(effect_types, body)` and `MaskBehind(effect_types, body)` when needed.

```
Current (busy boundary):           Target (re-entrant):

  yield Perform(E)                   yield Perform(E)
    │                                  │
    ▼                                  ▼
  handler walk:                      handler walk:
    caller chain MINUS busy            caller chain (all PromptBoundary segments)
    │                                  │
    ▼                                  ▼
  handler H is excluded              handler H is included
  (implicit mask)                    (re-entrant by default)

                                   To skip H explicitly:
                                     Mask([E], body)
                                       → MaskBoundary segment in caller chain
                                       → next PromptBoundary for E is skipped
                                     MaskBehind([E], body)
                                       → MaskBoundary { behind=true }
                                       → handler BEHIND override is skipped
```

### 1.1 Relationship to Existing Specs

- **SPEC-008 (Rust VM)**: INV-8 "Busy Boundary (Top-Only)" is **superseded** by this spec. Handler walk pseudocode in §dispatch is revised to use caller chain traversal (per R17). `start_dispatch` no longer references busy exclusion. `MaskBoundary` added as a `SegmentKind`.
- **SPEC-VM-010 (Non-terminal Delegate)**: Scope behavior section revised — handler body effects dispatch WITH the current handler, not without.
- **SPEC-TYPES-001 (Program/Effect Separation)**: References to "busy boundary" removed. `Eval` justification revised.
- **SPEC-VM-015 (Segment-Owned State)**: Unaffected. Segment ownership is orthogonal to handler visibility policy.
- **VM-FRAME-001 (Typed Frames)**: Orthogonal. Frame typing does not depend on visibility semantics.

### 1.2 Theoretical Basis

In Koka's effect system (Leijen 2017, Koka book §3.4.7):

1. **Deep handlers** are the default: when a handled computation resumes, the resumed computation runs under the same handler. This means the handler is naturally re-entrant — a resumed computation can yield the same effect again and it will be handled by the same handler.

2. **`mask<eff>`** is an explicit primitive that masks (hides) the innermost handler for effect `eff` within its body. The effect passes through to the next handler in the chain. This is opt-in, not default.

3. **`override`** is sugar for installing a handler and then masking the effect behind it (`mask behind<eff>`), so the new handler replaces the previous one. In doeff, this desugars to `handler { clauses }(MaskBehind(eff_types, body))`.

The doeff VM's current "busy boundary" implements the opposite: an implicit, always-on mask that cannot be disabled. This prevents legitimate use cases like:
- A logging handler that logs its own internal operations
- A state handler that reads its own state during state transitions
- An error handler that performs error-producing operations during recovery

---

## 2. Handler Visibility: Re-entrant by Default

> **SPEC-008 R17 alignment.** Handlers live on `PromptBoundary` segments. There is no `VM.handlers` map and no `scope_chain` array. Handler lookup walks the **caller chain** — the linked list of segments from the current segment upward through each segment's `caller` pointer. Each `PromptBoundary` encountered during the walk is a candidate handler. See SPEC-008 R17 §segment-kinds for the authoritative description.

### 2.1 Handler Walk — Re-entrant

Handler dispatch walks the caller chain from the current segment upward. Every `PromptBoundary` segment whose handler can match the effect is a candidate. **No busy exclusion** — the walk does not skip handlers that are currently executing.

```
handler_walk(current_seg, effect):
    seg = current_seg
    while seg is not None:
        match seg.kind:
            PromptBoundary { handler, .. }:
                if handler.can_handle(effect):
                    return seg          // found matching handler
            MaskBoundary { .. }:
                // see §3 for mask interaction
                ...
        seg = seg.caller
    raise UnhandledEffect
```

The entire busy-set computation from R1 is eliminated:
- No `dispatch_stack.last()` check
- No `HashSet<Marker>` busy set construction
- No `scope_chain` filtering
- No `VM.handlers` map lookup

### 2.2 `start_dispatch()` — Caller Chain Walk

```
start_dispatch(effect):
    lazy_pop_completed()
    matched_seg = handler_walk(current_segment(), effect)
    // capture continuation, push dispatch context …
```

The dispatch no longer constructs a `handler_chain` vector. The walk is a single traversal of the caller chain, stopping at the first matching `PromptBoundary` (subject to `MaskBoundary` skipping rules — see §3).

### 2.3 `DispatchContext` — Revised

With caller-chain-based dispatch, `DispatchContext` stores the matched segment rather than a handler chain index:

```
DispatchContext {
    dispatch_id:    DispatchId,
    effect:         Effect,
    prompt_seg_id:  SegmentId,    // the PromptBoundary that matched
    k_user:         Continuation,
    completed:      bool,
}
```

`Delegate` re-performs from the segment **above** `prompt_seg_id` in the caller chain — i.e., it resumes the handler walk from `prompt_seg_id.caller`, naturally skipping the current handler by position.

### 2.4 Semantic Consequence: Self-Dispatch

With re-entrant handlers, an effect yielded inside a handler body CAN match the same handler:

```python
def state_handler(effect, k):
    if isinstance(effect, GetEffect):
        # This yield Perform(Get(...)) will dispatch through
        # the caller chain, including this handler's own
        # PromptBoundary segment. If it handles Get, it recurses.
        current = yield Get("some-key")  # re-entrant!
        return (yield Resume(k, current))
    yield Delegate()
```

This is **correct and intentional**. If the programmer wants to avoid self-dispatch, they use `Mask` (§3):

```python
def state_handler(effect, k):
    if isinstance(effect, GetEffect):
        # Mask Get effects in this body — they skip this handler
        masked = Mask([GetEffect], Get("some-key"))
        current = yield masked
        return (yield Resume(k, current))
    yield Delegate()
```

### 2.5 Brief References (Detailed in SPEC-008 R17)

- **`return(x)` clause**: A `PromptBoundary` handler may define a `return` clause that transforms the final value when the handled body completes normally. See SPEC-008 R17 §return-clause.
- **`Finally(cleanup: DoExpr)`**: A DoCtrl that guarantees `cleanup` runs whether the body completes normally or is abandoned. See SPEC-008 R17 §finally.

---

## 3. Mask: Explicit Handler Skipping

> **R2 rewrite.** The R1 mask-stack model (per-segment `MaskEntry` stack, `is_masked()` helper) is replaced by **MaskBoundary segments** — mask is now a segment kind, not metadata on an existing segment. This aligns with SPEC-008 R17's segment-based architecture.

### 3.1 `Mask` and `MaskBehind` DoCtrl Definitions

```
/// Mask(effect_types, body) — evaluate body with the innermost handler
/// for each specified effect type skipped during the handler walk.
///
/// Koka equivalent: mask<eff> { body }
///
/// Creates a MaskBoundary segment (behind=false) as parent of a new
/// segment for body.
Mask {
    effect_types: List<EffectType>,   // effect type classes to mask
    body: DoExpr,                      // body to evaluate under the mask
}

/// MaskBehind(effect_types, body) — like Mask but with behind=true.
///
/// Koka equivalent: mask behind<eff> { body }
///
/// Used in the override handler desugaring. The "behind" flag shifts
/// the skip target: instead of skipping the FIRST matching
/// PromptBoundary for the masked effect, it skips the one BEHIND
/// (above) the override handler's PromptBoundary.
///
/// Desugaring: override handler { clauses }(body)
///           → handler { clauses }(MaskBehind(eff_types, body))
MaskBehind {
    effect_types: List<EffectType>,
    body: DoExpr,
}
```

### 3.2 MaskBoundary Segments

When the VM processes `Mask(effect_types, body)` or `MaskBehind(effect_types, body)`:

1. Create a **MaskBoundary segment** with:
   ```
   SegmentKind::MaskBoundary {
       masked_effects: Set<EffectType>,
       behind: bool,    // false for Mask, true for MaskBehind
   }
   ```
2. The MaskBoundary segment's `caller` points to the current segment
3. Create a child segment for `body`, whose `caller` points to the MaskBoundary segment
4. Begin evaluating `body` in the child segment
5. When `body` completes, the MaskBoundary segment is naturally popped (standard segment lifecycle)

The MaskBoundary segment is **not** a handler — it carries no handler function and no continuation. It exists solely to influence the handler walk.

### 3.3 Handler Walk with MaskBoundary

During handler dispatch, the caller chain walk encounters both `PromptBoundary` and `MaskBoundary` segments. When a `MaskBoundary` is encountered, it modifies how subsequent `PromptBoundary` segments are treated:

```
handler_walk(current_seg, effect):
    seg = current_seg
    skip_next: Set<EffectType> = {}     // effects whose next handler should be skipped
    skip_behind: Set<EffectType> = {}   // effects whose behind-handler should be skipped

    while seg is not None:
        match seg.kind:
            MaskBoundary { masked_effects, behind: false }:
                // Standard mask: skip the NEXT PromptBoundary for these effects
                skip_next = skip_next ∪ masked_effects

            MaskBoundary { masked_effects, behind: true }:
                // Behind mask: skip the handler BEHIND (above) the next one
                skip_behind = skip_behind ∪ masked_effects

            PromptBoundary { handler, .. }:
                for each effect_type that handler can handle:
                    if type(effect) == effect_type:
                        if effect_type ∈ skip_next:
                            // This handler is masked — skip it
                            skip_next = skip_next \ {effect_type}
                            continue to next seg
                        if effect_type ∈ skip_behind:
                            // This handler is NOT masked, but the one
                            // behind it will be. Move the skip target
                            // from skip_behind → skip_next.
                            skip_behind = skip_behind \ {effect_type}
                            skip_next = skip_next ∪ {effect_type}
                            return seg   // this handler matches
                        return seg       // normal match

        seg = seg.caller
    raise UnhandledEffect
```

**Key semantics:**
- `Mask([E], body)` → encountering the `MaskBoundary` adds `E` to `skip_next`. The first `PromptBoundary` handling `E` above the mask is skipped; the second one handles it.
- `MaskBehind([E], body)` → encountering the `MaskBoundary` adds `E` to `skip_behind`. The first `PromptBoundary` handling `E` is NOT skipped (it is the override handler). When the walk passes that handler, `E` moves from `skip_behind` to `skip_next`, so the NEXT handler above is skipped.

### 3.4 Python Surface API

```python
from doeff import Mask, MaskBehind

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

# MaskBehind — used in override pattern (see §4)
# Override handler's body runs under MaskBehind so that
# re-performed effects skip the OUTER handler
override_body = MaskBehind([LogEffect], inner_program())
```

### 3.5 Interaction with Delegate/Pass

`Mask`/`MaskBehind` are orthogonal to `Delegate` and `Pass`:

- **`Delegate()`** — non-terminal re-perform from the segment above `prompt_seg_id` in the caller chain. Already skips the current handler by position. Mask does not affect Delegate dispatch.
- **`Pass(effect?)`** — terminal pass-through. Already advances past the current handler. Mask does not affect Pass dispatch.
- **`Mask(types, body)` + `yield Perform(E)`** — the effect dispatches via the caller chain walk, but the next `PromptBoundary` for `E` above the `MaskBoundary` is skipped.

The key difference: Delegate/Pass skip by **position** (caller chain advancement from the current handler). Mask/MaskBehind skip by **effect type** (any dispatch originating within the masked body).

---

## 4. Override Handler Pattern

### 4.1 Concept

An "override" handler replaces an outer handler for the same effect type. In Koka:

```koka
fun override handler(action) {
    with handler { ... }      // install new handler
    mask behind<eff>(action)  // mask the handler BEHIND the new one
}
```

**Desugaring in doeff:**

```
override handler { clauses }(body)
  →  handler { clauses }(MaskBehind(eff_types, body))
```

The `MaskBehind` DoCtrl creates a `MaskBoundary` segment with `behind=true` around `body`. The `MaskBoundary` sits between the override handler's `PromptBoundary` and `body`'s segment in the caller chain. During the handler walk from within `body`:

1. The walk encounters the `MaskBoundary { behind=true }` — adds the effect types to `skip_behind`
2. The walk reaches the override handler's `PromptBoundary` — it matches, and moves the effect type from `skip_behind` to `skip_next`
3. The override handler handles the effect normally

If the override handler re-performs (via `Delegate`), the walk continues from above the override handler and the old outer handler is available — `Delegate` naturally skips the current handler by position.

The critical point: `MaskBehind` masks the handler **behind** the override (the outer handler that `body` would have reached without the override). Effects from `body` always reach the override handler first. The override handler then decides whether to forward via `Delegate`.

In doeff:

```python
# Override handler that observes and re-performs
def override_log_handler(effect, k):
    """Catches LogEffect, observes it, re-performs to outer handler."""
    if isinstance(effect, LogEffect):
        print(f"[observed] {effect}")          # observe
        result = yield Delegate()              # re-perform to outer
        return (yield Resume(k, result))
    yield Delegate()

# Desugaring of: override override_log_handler(body)
overridden = WithHandler(
    override_log_handler,
    MaskBehind([LogEffect], body)
)
```

### 4.2 `MaskBehind` vs `Mask` in Override

| Primitive | Skip target | Use case |
|-----------|-------------|----------|
| `Mask([E], body)` | First `PromptBoundary` for `E` above the `MaskBoundary` | Self-masking: handler skips itself |
| `MaskBehind([E], body)` | Second `PromptBoundary` for `E` above the `MaskBoundary` (the one *behind* the override) | Override: body effects reach the override handler, outer handler is skipped |

With `MaskBehind`, the override handler is **not** skipped — it handles effects from `body` normally. The handler it *replaced* (the one behind it) is the one that gets masked. This ensures `body`'s effects go to the override, and the override decides what to forward.

### 4.3 WithIntercept Supersession

> **WithIntercept is superseded.** The override handler pattern (handler + `MaskBehind`) replaces `WithIntercept` for all use cases. See SPEC-WITH-INTERCEPT deprecation notice.

The `WithIntercept` primitive allowed observing effects before handler dispatch. Every use case for interceptors can be expressed as an override handler:

**Use case: type-specific interception.** Observe a specific effect type without modifying accumulation.

```
Default handler stack: [in_memory_log_handler, handler_a, handler_b]
Goal: observe log effects from handler_a and handler_b by printing,
      while keeping the in_memory_log_handler's accumulation behavior.

Solution with override:
  override log handler {
      LogEffect(msg) → {
          print(msg)                  // observe
          yield Delegate()            // re-perform → goes to in_memory_log_handler
          yield Resume(k, result)
      }
  }(body_with_handler_a_and_handler_b)

Desugaring:
  WithHandler(
      observe_log_handler,
      MaskBehind([LogEffect],
          body_with_handler_a_and_handler_b
      )
  )
```

The `MaskBehind` ensures that `handler_a` and `handler_b`'s log effects reach the override handler (not the outer `in_memory_log_handler`). The override handler prints and then delegates to the outer handler via `Delegate`, which re-performs from the override handler's position in the caller chain — reaching `in_memory_log_handler`.

**Use case: catch-all observation.** Observe all effects regardless of type.

```python
def catch_all_observer(effect, k):
    """Observe every effect, then delegate."""
    print(f"[trace] {effect}")
    yield Delegate()
```

No mask needed — a catch-all handler with `Delegate` naturally observes and forwards.

**Why WithIntercept is unnecessary:** Every interceptor pattern decomposes into either (a) an override handler with `MaskBehind` for type-specific observation, or (b) a catch-all handler with `Delegate` for untyped observation. The override pattern is strictly more expressive because it composes with the standard handler walk, mask system, and continuation machinery.

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
Handlers are re-entrant by default. The handler walk traverses the
caller chain without filtering. Effects yielded during handler
execution dispatch through all PromptBoundary segments in the caller
chain, including the currently-handling handler.

Handler skipping is opt-in via Mask/MaskBehind DoCtrl, which create
MaskBoundary segments. During the handler walk, MaskBoundary segments
cause the next (or behind) PromptBoundary for the masked effect types
to be skipped.

WithIntercept is superseded by the override handler pattern (§4.3).

See SPEC-VM-016 for semantics.
```

---

## 6. Interaction with Existing Dispatch Features

### 6.1 Deep Handlers (Default)

doeff handlers are deep by default: when a continuation is resumed, the resumed computation runs under the same handler scope (the handler is reinstalled around the continuation). This is already correct and unchanged by this spec.

Re-entrant dispatch + deep handlers means: a resumed computation that yields the same effect will be handled by the same handler instance. This is the Koka default behavior.

### 6.2 Interceptors (`WithIntercept`)

`WithIntercept` is **superseded** by the override handler pattern (§4.3). The override handler + `MaskBehind` combination covers all interceptor use cases with better composability and no special-case dispatch path. See SPEC-WITH-INTERCEPT deprecation notice for migration guidance.

### 6.3 Scheduler Handler (`SPEC-SCHED-001`)

The cooperative scheduler handler handles `Spawn`, `Gather`, `Race`, etc. With re-entrant dispatch, a spawned task that yields `Spawn` will be handled by the same scheduler handler. This is correct — the scheduler should manage its own spawned tasks.

### 6.4 `Eval(expr, handlers)`

`Eval` creates a fresh scope with explicit handlers. `Eval` does not interact with `MaskBoundary` segments (the new segment created by `Eval` starts a fresh caller chain). `Eval`'s justification no longer includes "busy boundary avoidance" — its purpose is scoped handler installation.

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
- [ ] Handler walk traverses caller chain without busy filtering
- [ ] Busy set computation fully removed
- [ ] Effects yielded during handler execution can match the executing handler
- [ ] `DispatchContext` stores `prompt_seg_id` (no `handler_chain` vector)
- [ ] `Delegate` re-performs from `prompt_seg_id.caller` in caller chain

### Mask Primitive — MaskBoundary Segments
- [ ] `Mask` DoCtrl variant added to `DoCtrl` enum
- [ ] `MaskBehind` DoCtrl variant added to `DoCtrl` enum
- [ ] `Mask` Python class added as DoCtrl surface API
- [ ] `MaskBehind` Python class added as DoCtrl surface API
- [ ] `MaskBoundary` added as a `SegmentKind` with `masked_effects` and `behind` fields
- [ ] `Mask(eff_types, body)` creates `MaskBoundary { behind: false }` segment as parent of body segment
- [ ] `MaskBehind(eff_types, body)` creates `MaskBoundary { behind: true }` segment as parent of body segment
- [ ] Handler walk skips next `PromptBoundary` for masked effects when encountering `MaskBoundary { behind: false }`
- [ ] Handler walk defers skip to behind-handler when encountering `MaskBoundary { behind: true }`
- [ ] Nested masks compose correctly (multiple `MaskBoundary` segments in caller chain)
- [ ] `MaskBoundary` segments are naturally popped when body completes (standard segment lifecycle)

### WithIntercept Supersession
- [ ] `WithIntercept` DoCtrl removed from VM dispatch path
- [ ] Override handler + `MaskBehind` covers type-specific interception use case
- [ ] Catch-all handler + `Delegate` covers untyped observation use case
- [ ] SPEC-WITH-INTERCEPT deprecation notice references §4.3

### Spec Updates
- [ ] SPEC-008 INV-8 revised to reference SPEC-VM-016
- [ ] SPEC-008 handler walk pseudocode updated (caller chain, `MaskBoundary` interaction)
- [ ] SPEC-VM-010 §Scope behavior revised (re-entrant, not busy-excluded)
- [ ] SPEC-TYPES-001 "busy boundary" references removed

### Testing
- [ ] Test: handler yields same effect type → handled by same handler (re-entrant)
- [ ] Test: `Mask([EffType], body)` → effect dispatches to outer handler (skips first `PromptBoundary`)
- [ ] Test: `MaskBehind([EffType], body)` → effect dispatches to override handler, override re-perform skips outer handler
- [ ] Test: nested Mask → each `MaskBoundary` independently effective
- [ ] Test: Mask + Delegate → Delegate unaffected by Mask
- [ ] Test: 3-level handler nesting → all handlers visible at all levels
- [ ] Test: handler Resume → resumed computation sees same handler (deep handler)
- [ ] Test: override handler pattern — observe log effects from inner handlers, outer handler accumulates
- [ ] Test: `MaskBehind` in override desugaring — `handler { clauses }(MaskBehind(eff_types, body))` works correctly
- [ ] Test: `WithIntercept` usage replaced by override pattern (no interceptor dispatch path exercised)
- [ ] `cargo test --manifest-path packages/doeff-vm/Cargo.toml` — ALL pass
- [ ] `uv run pytest -q` — ALL pass

---

## 9. Open Questions

1. **Mask lifetime**: Should Mask be scoped to a DoExpr body (as specified), or should there be a "mask until end of handler" variant? Koka only has body-scoped mask. (Unchanged from R1.)

2. **MaskBoundary serialization**: If we add VM state serialization (for durable execution / SPEC-CORE-425), `MaskBoundary` segments need to be serializable. `masked_effects` contains Python type objects — how to serialize? (Unchanged from R1, updated terminology.)

3. **Performance**: The handler walk now traverses the caller chain and encounters `MaskBoundary` segments inline. Each `MaskBoundary` adds a set-union operation. For typical programs, the number of active `MaskBoundary` segments is very small (0–2). This is comparable to the R1 mask-stack approach and strictly better than the eliminated busy-set computation.

4. **WithIntercept migration**: Existing code using `WithIntercept` needs migration to the override pattern. Should we provide a compatibility shim that desugars `WithIntercept` to override + `MaskBehind`, or remove it outright? (New in R2.)

---

## 10. References

- Koka book §3.4.7 "Masking Effects" — https://koka-lang.github.io/koka/doc/book.html
- Leijen, D. (2017). "Type directed compilation of row-typed algebraic effects"
- SPEC-008: Rust VM (§dispatch, §INV-8)
- SPEC-VM-010: Non-terminal Delegate
- SPEC-TYPES-001: Program/Effect Separation
- SPEC-VM-015: Segment-Owned Execution State
