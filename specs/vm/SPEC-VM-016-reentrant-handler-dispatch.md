# SPEC-VM-016: Self-Excluding Handler Dispatch (OCaml Semantics)

## Status: Draft (Revision 3)

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

### Revision 3 Changelog

**Adopts OCaml self-excluding semantics. Cancels Mask/MaskBehind/Override. WithIntercept restored.**

| Tag | Section | Change |
|-----|---------|--------|
| **R3-A** | §1, §2 | **Self-excluding dispatch (OCaml semantics).** Handler clause bodies run "above" their own prompt. Effects from handler code do NOT re-enter the same handler. Replaces Koka's re-entrant-by-default model. |
| **R3-B** | §3, §4 | **Mask/MaskBehind/Override CANCELLED.** Not needed — self-excluding default already provides Koka's `override` behavior. Mask is only useful in Koka's re-entrant world. Sections retained for historical reference, marked as cancelled. |
| **R3-C** | §4.3, §6.2 | **WithIntercept RESTORED.** Analysis showed mask/override cannot replace WithIntercept for the observation use case (intercepting effects from handler clause bodies). WithIntercept is an observer; mask/override are routers. Different capabilities. |
| **R3-D** | §1.2 | **Theoretical basis updated.** References OCaml Multicore semantics (handler clause runs above prompt) and explains why Koka's model was rejected. |

---

## 1. Summary

This spec replaces the doeff VM's "busy boundary" handler visibility model with **self-excluding handler dispatch**, following OCaml Multicore's algebraic effect semantics.

**Previous model (busy boundary):** When a handler is executing, it is marked "busy" and excluded from `visible_handlers()` via a HashSet computation over the dispatch stack. This was an ad-hoc implementation that happened to approximate the correct semantics.

**Target model (THIS SPEC):** Handler clause bodies run "above" their own prompt — effects yielded during handler execution dispatch through the caller chain **excluding** the currently-handling handler's `PromptBoundary` segment. This is the OCaml Multicore model where the handler clause executes in the outer scope.

```
Previous (busy boundary):          Target (self-excluding):

  yield Perform(E)                   yield Perform(E)
    │                                  │
    ▼                                  ▼
  handler walk:                      handler walk:
    caller chain MINUS busy set        caller chain, skip active handler's prompt
    (HashSet computation)              (single SegmentId comparison)
    │                                  │
    ▼                                  ▼
  handler H is excluded              handler H is excluded
  (correct but ad-hoc)              (correct, principled — OCaml semantics)

                                   Handler clause code:
                                     perform(E) → dispatches ABOVE prompt
                                     → naturally reaches outer handler
                                     → Koka's "override" behavior for free

                                   Resumed body code (deep handler):
                                     perform(E) → dispatches INSIDE prompt
                                     → sees the handler (re-entrant via continuation)
```

### 1.1 Relationship to Existing Specs

- **SPEC-008 (Rust VM)**: INV-8 "Busy Boundary (Top-Only)" is **superseded** by this spec. Handler walk pseudocode in §dispatch is revised to use caller chain traversal (per R17). `start_dispatch` no longer references busy exclusion. `MaskBoundary` added as a `SegmentKind`.
- **SPEC-VM-010 (Non-terminal Delegate)**: Scope behavior section revised — handler body effects dispatch WITH the current handler, not without.
- **SPEC-TYPES-001 (Program/Effect Separation)**: References to "busy boundary" removed. `Eval` justification revised.
- **SPEC-VM-015 (Segment-Owned State)**: Unaffected. Segment ownership is orthogonal to handler visibility policy.
- **VM-FRAME-001 (Typed Frames)**: Orthogonal. Frame typing does not depend on visibility semantics.

### 1.2 Theoretical Basis

In OCaml Multicore's effect system (Sivaramakrishnan et al., PLDI 2021 "Retrofitting Effect Handlers onto OCaml"):

1. **Handler clause bodies run above their own prompt.** When a handler catches an effect, the clause code executes on the original stack (above the handler). Effects performed by the clause code dispatch to handlers above the current one — the handler does NOT see itself.

2. **Deep handlers reinstall around the continuation.** When `continue k v` resumes the captured computation, the handler is re-wrapped around the continuation. Effects from the resumed computation DO see the handler (re-entrant via continuation).

3. **No `mask` primitive needed.** Because handler clause bodies naturally exclude themselves, Koka's `override` behavior is the default. There is no need for `mask`/`mask behind` primitives to control self-dispatch.

The formal reduction rules (Xavier Leroy, "Control Structures" §10.5):
```
Deep handler:  handle D[perform v] with eret, eeff  →  eeff(v, λx. handle D[x] with eret, eeff)
               ^^^^^^^                                  ^^^^                ^^^^^^^^^^^^^^^^^^^^^^^^
               handler clause body runs WITHOUT          continuation k has handler RE-INSTALLED
               handler in scope
```

**Why not Koka semantics (re-entrant by default)?**

Koka's model requires `mask`/`override` primitives to escape self-reentrance. Analysis showed:
- Most real Koka code uses `override` for transform-and-delegate patterns — i.e., most users want self-exclusion
- The self-reentrant default (`<emit,emit|e>` duplicate labels) creates ugly types and accidental infinite loops
- doeff's `WithIntercept` (observation of effects from handler clause bodies) **cannot** be replaced by mask/override — they are fundamentally different capabilities (observer vs router)
- Adopting OCaml semantics keeps WithIntercept viable and gives override behavior for free

---

## 2. Handler Visibility: Self-Excluding (OCaml Semantics)

> **SPEC-008 R17 alignment.** Handlers live on `PromptBoundary` segments. There is no `VM.handlers` map and no `scope_chain` array. Handler lookup walks the **caller chain** — the linked list of segments from the current segment upward through each segment's `caller` pointer. Each `PromptBoundary` encountered during the walk is a candidate handler. See SPEC-008 R17 §segment-kinds for the authoritative description.

### 2.1 Handler Walk — Self-Excluding

Handler dispatch walks the caller chain from the current segment upward. Every `PromptBoundary` segment whose handler can match the effect is a candidate, **except** the currently-active handler's own `PromptBoundary` segment (self-exclusion).

```
handler_walk(current_seg, effect, active_handler_seg_id):
    seg = current_seg
    while seg is not None:
        match seg.kind:
            PromptBoundary { handler, .. }:
                if seg.id == active_handler_seg_id:
                    // Self-exclusion: skip the active handler's own prompt
                    seg = seg.caller
                    continue
                if handler.can_handle(effect):
                    return seg          // found matching handler
        seg = seg.caller
    raise UnhandledEffect
```

The busy-set computation from R1 is eliminated and replaced by a single SegmentId comparison:
- No `dispatch_stack.last()` check
- No `HashSet<Marker>` busy set construction
- No `scope_chain` filtering
- No `VM.handlers` map lookup
- Instead: `active_handler_seg_id` comparison during walk

### 2.2 `start_dispatch()` — Caller Chain Walk

```
start_dispatch(effect):
    lazy_pop_completed()
    active_seg = get_active_handler_seg_id()  // from current dispatch context, if any
    matched_seg = handler_walk(current_segment(), effect, active_seg)
    // capture continuation, push dispatch context …
```

The `active_handler_seg_id` is the SegmentId of the PromptBoundary whose handler clause is currently executing. When no handler clause is active (dispatching from body code), `active_seg` is None and all handlers are candidates.

### 2.3 `DispatchContext` — Revised

`DispatchContext` stores the matched segment and tracks the active handler for self-exclusion:

```
DispatchContext {
    dispatch_id:        DispatchId,
    effect:             Effect,
    prompt_seg_id:      SegmentId,          // the PromptBoundary that matched
    active_handler_seg_id: SegmentId,       // for self-exclusion during nested dispatch
    k_user:             Continuation,
    handler_chain:      Vec<Marker>,        // implementation detail for completion tracking
    handler_idx:        usize,
    completed:          bool,
}
```

`Delegate` re-performs from the segment **above** `prompt_seg_id` in the caller chain — i.e., it resumes the handler walk from `prompt_seg_id.caller`, naturally skipping the current handler by position.

### 2.4 Semantic Consequence: Self-Exclusion

With self-excluding handlers, an effect yielded inside a handler body does NOT match the same handler:

```python
def logging_handler(effect, k):
    if isinstance(effect, LogEffect):
        # This Log goes to the OUTER handler, not this one.
        # Handler clause body runs "above" its own prompt.
        yield Log("handling: " + effect.msg)  # → outer Log handler
        return (yield Resume(k, None))
    yield Delegate()
```

This is **correct and intentional** (OCaml semantics). The handler naturally forwards to the outer handler without needing `Mask` or `override` — Koka's `override` behavior is the default.

For the resumed body code (via `Resume(k, ...)`), the handler IS visible because deep handlers reinstall around the continuation:

```python
def counting_handler(effect, k):
    if isinstance(effect, LogEffect):
        # After resume, if body performs Log again, THIS handler catches it
        return (yield Resume(k, None))  # body sees this handler (deep)
    yield Delegate()
```

### 2.5 Brief References (Detailed in SPEC-008 R17)

- **`return(x)` clause**: A `PromptBoundary` handler may define a `return` clause that transforms the final value when the handled body completes normally. See SPEC-008 R17 §return-clause.
- **`Finally(cleanup: DoExpr)`**: A DoCtrl that guarantees `cleanup` runs whether the body completes normally or is abandoned. See SPEC-008 R17 §finally.

---

## 3. ~~Mask: Explicit Handler Skipping~~ (CANCELLED — R3)

> **R3: CANCELLED.** Mask/MaskBehind are not needed. OCaml self-excluding semantics (§2) provide Koka's `override` behavior as the default. The mask primitive is only useful in Koka's re-entrant-by-default world where you need to escape self-dispatch. Since doeff uses self-excluding semantics, mask has no use case. This section is retained for historical reference only.

> ~~**R2 rewrite.** The R1 mask-stack model (per-segment `MaskEntry` stack, `is_masked()` helper) is replaced by **MaskBoundary segments** — mask is now a segment kind, not metadata on an existing segment. This aligns with SPEC-008 R17's segment-based architecture.~~

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

## 4. ~~Override Handler Pattern~~ (CANCELLED — R3)

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

### 4.3 ~~WithIntercept Supersession~~ (REVERSED — R3)

> **R3: WithIntercept is RESTORED.** Analysis showed that mask/override CANNOT replace WithIntercept for the observation use case. The R2 claim that "every interceptor pattern decomposes into override + MaskBehind" is **incorrect**.

**Why WithIntercept cannot be replaced by mask/override:**

The critical use case is intercepting effects from handler clause bodies:

```
Handler stack: [writer(Log), handler_a(EffA), handler_b(EffB), body]

handler_a's clause body performs Log → walks up → writer catches it
handler_b's clause body performs Log → walks up → writer catches it

User wants to observe these Log effects (print to stdout)
while keeping writer's accumulation.
```

Neither mask nor override can intercept these Log effects because:
- Handler clause bodies run **above** their own prompt (OCaml semantics)
- Their effects dispatch to handlers above them in the caller chain
- Any handler installed **below** them (between handler_b and body) cannot see effects from handler clause bodies above

**WithIntercept** is fundamentally an **observer** — it sees effects flowing through a section of the handler chain without handling them. mask/override are **routers** — they change which handler catches an effect. These are different capabilities:

| Mechanism | Capability | Can intercept handler clause body effects? |
|-----------|-----------|-------------------------------------------|
| WithIntercept | Observer (sees effects in transit) | YES |
| mask/override | Router (redirects effect dispatch) | NO |
| Handler restructuring | Insert handler between layers | YES (but requires control of installation order) |

WithIntercept remains a first-class VM primitive. SPEC-WITH-INTERCEPT deprecation is **reversed**.

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

**INV-8 (Revised): Self-Excluding Handler Dispatch**
```
Handler clause bodies run above their own prompt (OCaml semantics).
The handler walk traverses the caller chain, skipping the active
handler's own PromptBoundary segment (self-exclusion via
active_handler_seg_id comparison).

Effects yielded during handler execution dispatch to handlers ABOVE
the current handler. Effects from resumed body code (via deep handler
continuation reinstallation) see the handler normally.

WithIntercept remains a first-class VM primitive for effect observation.

See SPEC-VM-016 for semantics.
```

---

## 6. Interaction with Existing Dispatch Features

### 6.1 Deep Handlers (Default)

doeff handlers are deep by default: when a continuation is resumed, the resumed computation runs under the same handler scope (the handler is reinstalled around the continuation). This is already correct and unchanged by this spec.

Re-entrant dispatch + deep handlers means: a resumed computation that yields the same effect will be handled by the same handler instance. This is the Koka default behavior.

### 6.2 Interceptors (`WithIntercept`)

`WithIntercept` **remains a first-class VM primitive** (R3 reversal). The override pattern cannot replace WithIntercept for observing effects from handler clause bodies — see §4.3 for full analysis. WithIntercept's `InterceptorState`, `InterceptBoundary`, and related machinery are retained.

### 6.3 Scheduler Handler (`SPEC-SCHED-001`)

The cooperative scheduler handler handles `Spawn`, `Gather`, `Race`, etc. With re-entrant dispatch, a spawned task that yields `Spawn` will be handled by the same scheduler handler. This is correct — the scheduler should manage its own spawned tasks.

### 6.4 `Eval(expr, handlers)`

`Eval` creates a fresh scope with explicit handlers. `Eval` does not interact with `MaskBoundary` segments (the new segment created by `Eval` starts a fresh caller chain). `Eval`'s justification no longer includes "busy boundary avoidance" — its purpose is scoped handler installation.

---

## 7. Migration Path

### 7.1 Behavioral Change

This is a **refinement**, not a semantic change. The busy-boundary model happened to approximate self-excluding semantics. The new implementation is more principled (single SegmentId comparison vs HashSet computation) but produces the same observable behavior:

| Scenario | Before (busy boundary) | After (self-excluding) |
|----------|------------------------|------------------------|
| Handler yields same effect type | Dispatches to outer handler | Dispatches to outer handler (same) |
| Handler yields different effect type | Dispatches normally | Dispatches normally (same) |
| Handler yields Delegate() | Skips current handler | Skips current handler (same) |
| Handler yields Pass(effect) | Skips current handler | Skips current handler (same) |
| Body code resumes and yields | Handler catches it (deep) | Handler catches it (deep, same) |

### 7.2 No Breaking Changes

Self-excluding semantics match the previous busy-boundary behavior for all practical cases. No handler migration is needed.

### 7.3 Migration Checklist

1. ~~Audit all handler implementations for self-effect yields~~ Not needed — behavior unchanged
2. ~~Add Mask or Delegate where outer forwarding was intended~~ Not needed — self-exclusion is default
3. Verify handler composition with nested `WithHandler` stacks (regression testing)

---

## 8. Acceptance Criteria

### Core Semantics
- [x] Handler walk traverses caller chain with self-exclusion (active_handler_seg_id comparison)
- [x] Busy set computation fully removed (replaced by single SegmentId comparison)
- [x] Effects yielded during handler execution dispatch to OUTER handlers (self-excluding)
- [x] `DispatchContext` stores `prompt_seg_id` and `active_handler_seg_id`
- [x] `Delegate` re-performs from `prompt_seg_id.caller` in caller chain

### ~~Mask Primitive~~ (CANCELLED — R3)
~~All mask/MaskBehind/MaskBoundary items cancelled. Not needed with OCaml self-excluding semantics.~~

### WithIntercept (RESTORED — R3)
- [x] `WithIntercept` remains in VM dispatch path (not superseded)
- [x] InterceptorState, InterceptBoundary machinery retained
- [x] SPEC-WITH-INTERCEPT deprecation reversed

### Spec Updates
- [x] SPEC-008 INV-8 revised to reference SPEC-VM-016 (self-excluding)
- [ ] SPEC-008 handler walk pseudocode updated (caller chain, self-exclusion)
- [ ] SPEC-VM-010 §Scope behavior revised (self-excluding, not busy-excluded)
- [ ] SPEC-TYPES-001 "busy boundary" references removed

### Testing
- [x] Test: handler yields same effect type → dispatches to outer handler (self-excluding)
- [x] Test: 3-level handler nesting → handler clause body effects go to outer handlers
- [x] Test: handler Resume → resumed computation sees same handler (deep handler)
- [x] Test: WithIntercept continues to work for observation use case
- [x] `cargo test --manifest-path packages/doeff-vm/Cargo.toml` — 220 passed
- [x] `uv run pytest -q` — 754 passed

---

## 9. Open Questions

1. ~~**Mask lifetime**~~: RESOLVED (R3) — Mask cancelled. Not applicable.

2. ~~**MaskBoundary serialization**~~: RESOLVED (R3) — MaskBoundary cancelled. Not applicable.

3. **Performance**: The handler walk now uses a single `active_handler_seg_id` comparison instead of HashSet busy-set computation. This is strictly better than the previous model. No `MaskBoundary` segments to traverse (cancelled).

4. ~~**WithIntercept migration**~~: RESOLVED (R3) — WithIntercept stays. No migration needed.

5. **DispatchContext cleanup** (New in R3): `DispatchContext` still carries `handler_chain: Vec<Marker>` for completion tracking. Consider whether this can be simplified now that mask/override are cancelled. Low priority — it works correctly.

---

## 10. References

- Sivaramakrishnan et al. (2021). "Retrofitting Effect Handlers onto OCaml" (PLDI 2021)
- Xavier Leroy, "Control Structures" §10.5 — handler clause reduction semantics
- Koka book §3.4.7-§3.4.8 "Masking Effects" / "Overriding Handlers" — https://koka-lang.github.io/koka/doc/book.html (reference for why Koka model was rejected)
- Leijen, D. (2017). "Type directed compilation of row-typed algebraic effects"
- SPEC-008: Rust VM (§dispatch, §INV-8)
- SPEC-VM-010: Non-terminal Delegate
- SPEC-TYPES-001: Program/Effect Separation
- SPEC-VM-015: Segment-Owned Execution State
