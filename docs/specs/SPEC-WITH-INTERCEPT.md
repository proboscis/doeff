# SPEC: WithIntercept — Cross-Cutting Effect Observation

**Status**: DEPRECATED — Superseded by SPEC-VM-016 R2 (Override Handler Pattern)  
**Date**: 2026-02-25

---

## DEPRECATION NOTICE

**This spec is superseded.** The `WithIntercept` DoCtrl primitive and all associated VM machinery (`InterceptorState`, `InterceptBoundary` segments, `interceptor_eval_depth`, `interceptor_skip_stack`) are removed in SPEC-008 R17.

**Replacement**: The override handler pattern (SPEC-VM-016 R2 §4) provides strictly more powerful interception semantics:

- **Type-specific interception**: Override handler catches the effect, observes/transforms it, re-performs under `MaskBehind` to delegate to the outer handler.
- **Catch-all observation**: Catch-all handler (can_handle → true for all effects) + `Delegate()` to forward to outer handlers.
- **Cross-cutting observation of handler-emitted effects**: Override handler installed outside the default handler stack observes effects yielded by inner handlers during their execution.

The specific motivating use case from this spec — observing effects emitted by handlers above an observer in the stack — is solved by the override pattern because the override handler is installed as the innermost handler for the observed effect type, catching effects before the original handler.

**Historical context**: This spec is retained for historical reference. The analysis in "Why Intercept cannot be an Effect" (§Design Rationale) remains valid for the old continuation model. The override pattern avoids the problem entirely by using standard handler semantics (handler + mask) instead of a separate interception mechanism.

See: SPEC-008 R17 (R17-F), SPEC-VM-016 R2 (§4.3)

---

### Revision 3 Changelog

Changes from Rev 2. Interceptor invocation expressed as `Eval(Apply(f, [effect]))`. Depends on ASTStream-as-DoExpr unification (see SPEC-008 Rev 16).

| Tag | Section | Change |
|-----|---------|--------|
| **R3-A** | Interceptor invocation | **`Eval(Apply(f, [effect]))` replaces Expand-based invocation.** `f` is any callable that returns `DoExpr`. `Apply(f, [effect])` calls `f`, producing a DoExpr (which may be an ASTStream or static IR). `Eval` evaluates the result. No Apply-vs-Expand distinction needed. |
| **R3-B** | `f` type | **`f` is a callable that returns `DoExpr`, not a specific type.** Not constrained to `DoeffGeneratorFn` or any Rust-specific type. Any callable that takes an effect and returns a DoExpr. The VM does not sniff the type of `f`. |
| **R3-C** | Depends on | **ASTStream as DoExpr.** This spec depends on SPEC-008 Rev 16 which promotes ASTStream to a DoExpr variant. `Eval` can process both static DoExpr nodes and streaming ASTStream programs uniformly. |
| **R3-D** | Macro expansion model | **Interceptor invocation is macro expansion.** `Apply(f, [effect])` = expand the macro `f` with argument `effect`, producing IR. `Eval` = evaluate the produced IR. This is the same pattern as `@do` generator expansion, unified through the IR. |

### Revision 2 Changelog

Changes from Rev 1. Promotes interceptor to first-class IR; removes type filtering from VM.

| Tag | Section | Change |
|-----|---------|--------|
| **R2-A** | IR node | **`WithIntercept(f, expr)` — two parameters only.** `types` and `mode` removed from the IR. The VM invokes `f` on every effect within scope. Type filtering is a Python-side convenience wrapper. |
| **R2-C** | VM boundary | **No `is_do_callable` probing.** The `interceptor_call_arg` hack that sniffed for `DoeffGeneratorFn` vs plain callable is eliminated. `f` is always invoked uniformly via `Eval(Apply(...))`. Resolves VM-INTERCEPT-003. |
| **R2-D** | Python wrappers | **Type filtering is Python-side sugar.** `with_intercept(f, expr, types, mode)` wraps `f` in a filtering binder and returns `WithIntercept(wrapped_f, expr)`. The VM does not know about types or modes. |
| **R2-E** | Design rationale | **Added: Why Intercept cannot be an Effect.** Documents the continuation-model vs stack-model analysis that confirms WithIntercept must be DoCtrl. Supersedes SPEC-EFF-004's effect-based Intercept model for cross-cutting observation. |

---

## Problem

In doeff's handler stack, effects propagate upward. A handler below cannot observe effects emitted by handlers above it. This makes cross-cutting concerns like logging, tracing, and auditing impractical without intimate knowledge of the handler stack topology.

```
┌──────────────────────┐
│  writer (Tell)       │  ← consumes Tell
├──────────────────────┤
│  handler_A           │  ← internally yields Tell("from A") → goes UP
├──────────────────────┤
│  printer (observer)  │  ← never sees handler_A's Tell
├──────────────────────┤
│  user_program        │  ← its Tell is seen by printer ✓
└──────────────────────┘
```

The user must manually position observers above all emitters — a fragile, non-composable requirement that violates the effect system's promise of modular composition.

### Why handler stacking alone is insufficient

Every attempt to solve this within pure handler stacking hits the same wall: **you cannot insert observation logic into another handler's upward dispatch path without being above it.** Approaches explored and rejected:

- **Stack ordering**: Works but requires users to know the full stack topology.
- **Tagging + delegation**: Tags can only be applied below the emitter; handler-emitted effects never pass through the tagger.
- **Polysemy's `intercept`**: Exists due to Haskell's type-level effect rows (`interpret` removes the effect type, so re-performing is impossible). In doeff, `Delegate` already provides intercept semantics within the stack — the limitation is positional, not semantic.

### Why Intercept cannot be an Effect [R2-E]

The original SPEC-EFF-004 modeled Intercept as an `InterceptEffect` handled by pushing an `InterceptFrame` onto the continuation. This worked in the old Python runtime where handlers could directly manipulate continuation frames (`ContinueProgram` with modified K). The Rust VM's handler protocol does not expose this — handlers Resume/Delegate/Pass; they do not manipulate K directly.

Three fundamental barriers prevent effect-based implementation:

1. **Replacement program scope**: When a transform returns a replacement Program, that Program's effects must also pass through the same interceptor (per Rev 1 semantics). In a handler-based approach, the handler is already mid-invocation processing one effect — there is no persistent scope to intercept effects from the replacement Program without recursively re-installing the handler.

2. **Re-dispatch escapes**: When a handler transforms effect `E` into `E'` and re-dispatches via `Perform(E')`, the new effect goes to outer handlers — it escapes the intercept scope. The interceptor cannot observe its own transformed effects' downstream behavior.

3. **Scope vs invocation**: Handlers are invoked per-effect and terminate. Interceptors establish a persistent scope across an entire sub-computation. This is a segment/scope concept that maps naturally to DoCtrl, not to the handler invocation protocol.

This is a consequence of doeff's continuation-based computation model. In a stack-based model where handlers can push/pop frames, Intercept could be an Effect. doeff chose continuations; WithIntercept is DoCtrl.

---

## Design

### IR Node [R2-A]

```
WithIntercept(f, expr)
```

A `DoCtrl` AST node (control-flow primitive, same level as `WithHandler`). When `expr` is evaluated, every effect yielded during execution is passed to `f` before normal dispatch proceeds. This includes yields originating from handlers within the scope — not just the user program.

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `f` | `Effect -> DoExpr` | Callable that takes an effect and returns a `DoExpr`. Not constrained to any specific type — any callable that satisfies this contract. The VM invokes it via `Eval(Apply(f, [effect]))`. [R3-B] |
| `expr` | `DoExpr` | The scoped program (and its handler chain) to observe. |

### Interceptor invocation model [R3-A, R3-D]

Interceptor invocation is **macro expansion**:

```
Eval(Apply(f, [effect]))
     │
     └→ Apply(f, [effect])  =  call f(effect)  →  DoExpr
                                                      │
                                                      └→ Eval processes the DoExpr
                                                         (static node OR ASTStream)
```

1. `Apply(f, [effect])` — calls `f` with the intercepted effect. `f` returns a `DoExpr`. This is macro expansion: `f` is the macro, the effect is the input, the DoExpr is the output AST.
2. `Eval(doexpr)` — evaluates the returned DoExpr. If it's an ASTStream (e.g., from a `@do` generator), the VM steps through it, pushing `Frame::Program`. If it's a static DoExpr node (e.g., `Pure(effect)` for pass-through), the VM evaluates it directly.
3. The result of evaluation becomes the effect that enters normal handler dispatch.

Because the invocation goes through `Eval`, the interceptor program is visible to `GetCallStack` when it's an ASTStream (pushed as `Frame::Program`). This resolves the call-stack incompleteness bug.

**Key property**: The VM does not need to distinguish whether `f` returns a generator or a static DoExpr. `Apply` gets the DoExpr, `Eval` processes it — the same path handles both. No `is_do_callable` sniffing, no Apply-vs-Expand decision. [R3-A]

**Dependency**: This model requires ASTStream to be a DoExpr variant so that `Eval` can process it. See SPEC-008 Rev 16. [R3-C]

### Semantics

1. **Scope**: `f` observes all yield points within `expr`. This includes:
   - Effects yielded by the user program.
   - Effects yielded by handlers during their execution (e.g., a handler that internally yields `Tell` while processing another effect).
   - Effects yielded by nested handlers at any depth.
   
   This is what makes `WithIntercept` cross-cutting — it sees effects regardless of their origin within the scope, including the handler chain itself.

2. **No type filtering at VM level** [R2-A]: The VM invokes `f` on every effect within scope. There is no VM-level `isinstance` check. `f` itself decides whether to act on the effect or pass it through unchanged. Python-side wrappers provide type filtering as sugar (see §Python API).

3. **No re-entrancy**: Effects yielded by `f` itself skip the interceptor that invoked `f`. This mirrors handler semantics — a handler's own internal effects do not re-enter the same handler.

4. **Non-consuming**: After `f` completes, the value it returns becomes the effect that enters normal handler dispatch. To observe without transforming, `f` returns the original effect. To transform, `f` returns a different effect.

5. **Nesting**: Multiple `WithIntercept` layers compose. Each interceptor's own yields skip only its own layer, not outer interceptors.

```
WithIntercept(f1,
  WithIntercept(f2,
    expr
  )
)

# expr yields Tell  → f2 sees it first, returns E' → f1 sees E'
# f2 yields Get     → f1 sees it (f2's yields skip f2, not f1)
```

6. **Effect transformation**: `f` receives the original effect and returns a value via its program. The returned value becomes the effect that enters normal dispatch. This allows:
   - **Observation**: `f` returns the original effect unchanged.
   - **Transformation**: `f` returns a modified effect.
   - **Suppression**: `f` returns a no-op (effect never reaches handlers). *(Open question: should suppression be supported, or should the original effect always dispatch?)*

### Dispatch flow

```
program yields effect E
    │
    ▼
VM: Eval(Apply(f, [E]))
    │
    ├── Apply(f, [E]) → f(E) → DoExpr
    │
    ├── Eval(DoExpr):
    │     if ASTStream → push Frame::Program, step through
    │     if static    → evaluate directly
    │
    │     f may yield its own effects → skip THIS interceptor,
    │                                   enter outer interceptors + handler stack
    │
    └── f returns E' (possibly == E)
          │
          ▼
    normal handler dispatch with E'
```

---

## Python API [R2-D]

Type filtering is Python-side sugar. The VM IR node is always `WithIntercept(f, expr)`.

### Raw IR construction

```python
import doeff_vm

# Intercept every effect — f decides what to do
program = doeff_vm.WithIntercept(my_observer, body())
```

### Convenience wrapper with type filtering

```python
from doeff import with_intercept

# Only intercept WriterTellEffect
program = with_intercept(
    my_observer,
    body(),
    types=(WriterTellEffect,),
    mode="include",
)
```

Implementation: `with_intercept` wraps `my_observer` in a filtering callable:

```python
def with_intercept(f, expr, types=(), mode="include"):
    @do
    def filtered_f(effect: Effect):
        match = isinstance(effect, types)
        if (mode == "include" and match) or (mode == "exclude" and not match):
            return (yield f(effect))
        return effect  # pass through unchanged

    return doeff_vm.WithIntercept(filtered_f, expr)
```

The VM sees only `WithIntercept(filtered_f, expr)`. No `types`, no `mode`.

### Binder contract

`f` is any callable that takes an effect and returns a `DoExpr`. Common implementations:

**Python `@do` function** (returns generator → ASTStream → DoExpr):
```python
@do
def my_observer(effect: Effect) -> Program[Effect]:
    yield slog(observed=str(effect))
    return effect  # pass through unchanged
```

The `effect: Effect` type annotation ensures the binder receives the effect as `Pure(effect)` — the raw DoExpr value, not a Perform-dispatched result.

**Rust callable** (implements `HandlerInvoke`-like trait, returns DoExpr directly):
The Rust interceptor returns a `DoExpr` when called — same pattern as `HandlerInvoke::invoke`. The VM processes the returned DoExpr via `Eval`.

**Plain Python function** (returns a static DoExpr):
```python
def identity_interceptor(effect):
    return doeff_vm.Pure(effect)  # pass through, no side effects
```

All three work uniformly through `Eval(Apply(f, [effect]))`. The VM doesn't care which one `f` is.

---

## VM Implementation Notes

### Changes from Rev 1

1. **`PyWithIntercept`**: Two fields only — `f` (callable), `expr` (DoExpr). Validate `f` is callable at construction. No type constraint beyond callable. Remove `types` and `mode` fields.

2. **`DoCtrl::WithIntercept`**: Two fields — `interceptor`, `expr`. Remove `types`, `mode`, `metadata`.

3. **`InterceptorEntry`**: Stores the interceptor callable. Remove `types`, `mode` fields.

4. **Interceptor invocation**: Replace `start_interceptor_invocation_mode`'s `DoCtrl::Apply(f, [effect])` → callback → `handle_interceptor_apply_result` with `Eval(Apply(f, [effect]))`. The `Eval` processes whatever DoExpr `f` returns — ASTStream or static.

5. **Delete `interceptor_call_arg`**: The `is_do_callable` hack is eliminated. All interceptors are invoked uniformly via `Apply`. Resolves VM-INTERCEPT-003.

6. **Depends on ASTStream-as-DoExpr**: `Eval` must be able to process ASTStream as a DoExpr variant. See SPEC-008 Rev 16.

---

## Theoretical Context

No existing algebraic effect system provides this exact primitive. Related work:

| Work | Relationship |
|------|-------------|
| **Tunneling** (Zhang & Myers, POPL 2019) | Modifies dispatch so effects *bypass* handlers. WithIntercept is the dual — effects are *observed by* an interceptor without being consumed. |
| **Bidirectional Effects** (Zhang et al., OOPSLA 2020) | Effects can flow back to the call site. Precedent for non-upward dispatch in effect systems. |
| **Named Handlers** (Xie & Leijen, 2021) | Operations target specific handlers by name, bypassing stack ordering. Similar goal (break stack constraint) but requires emitter awareness. |
| **Polysemy `intercept`** (Haskell) | Observes effects without removing them from the type-level row. Motivated by type system constraints that doeff does not have. |
| **Alegre thesis** (Saskatchewan, 2025) | "Capturing cross-cutting concerns in agent-based models using computational effects." Directly addresses this problem space. |

The framing: if tunneling ("effect bypasses handler") is formally sound, its dual ("effect is observed by interceptor without being consumed") should be formalizable with the same machinery.

---

## Open Questions

1. **Suppression**: Should `f` be able to suppress the effect entirely (prevent it from reaching handlers), or should the original effect always dispatch regardless of `f`'s return value?

2. **Continuation interaction**: When `f` transforms effect `E` into `E'`, the continuation `k` still expects a result matching `E`'s type. Should the VM enforce that `E'` is the same effect type as `E`, or allow arbitrary transformation?

3. **Ordering with handlers**: If `WithIntercept` wraps a `WithHandler`, the interceptor sees effects before handlers. If `WithHandler` wraps `WithIntercept`, handlers see effects first (and may delegate, in which case the interceptor on delegated effects is TBD). Define the exact interaction.

4. **Formal semantics**: Develop a small-step operational semantics for `WithIntercept` to ensure soundness. The tunneling paper's logical-relations model may serve as a starting point for the dual.

---

## Superseded

- **SPEC-EFF-004 §Intercept**: The effect-based `InterceptEffect` / `InterceptFrame` model is superseded for cross-cutting observation. `InterceptEffect` may still exist for the simpler Python-level `program.intercept(transform)` API, which does not require cross-cutting scope. `WithIntercept` is the VM-level primitive for cross-cutting concerns.
