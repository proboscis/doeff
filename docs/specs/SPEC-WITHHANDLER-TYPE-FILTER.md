# SPEC: WithHandler Type Filtering — Annotation-Driven Effect Dispatch

**Status**: DRAFT  
**Date**: 2026-03-01  
**Depends on**: VM-DEBT-008 (WithIntercept type filtering in Rust VM)

---

## Summary

Add type filtering to `WithHandler` dispatch, derived automatically from the handler function's `effect` parameter type annotation. The Rust VM skips calling a handler for effects that don't match the annotated types. This eliminates Python round-trips for non-matching effects while preserving full isinstance semantics including subclass relationships.

**User-facing change**: None. Handlers already annotate their `effect` parameter. The change is an automatic optimization — the VM reads the annotation and uses it to pre-filter dispatch.

---

## Motivation

### Current behavior

Every effect dispatched within a `WithHandler` scope calls the Python handler function, even when the handler's first action is an isinstance check followed by `yield Pass()`:

```python
@do
def my_handler(effect: Tell | Ask, k):
    if isinstance(effect, Tell):
        return (yield Resume(k, None))
    if isinstance(effect, Ask):
        return (yield Resume(k, config[effect.key]))
    yield Pass()  # ← reached for every non-Tell/non-Ask effect
```

For a program that raises 50 different effect types, this handler is called 50 times but only handles 2. The remaining 48 calls each cross the Rust→Python→Rust boundary just to execute `yield Pass()`.

### Proposed behavior

The VM reads the handler's `effect: Tell | Ask` annotation at `WithHandler` construction time and extracts `types=(Tell, Ask)`. At dispatch time, the VM performs `isinstance(effect, (Tell, Ask))` before calling the Python handler. Non-matching effects skip the handler entirely — zero Python overhead.

```
Before:  effect → call Python handler → isinstance → Pass → return to VM → next handler
After:   effect → VM isinstance check → no match → next handler (no Python call)
```

---

## Design

### Three-layer architecture

```
Layer 1 — Python API (user-facing):
    WithHandler(handler, expr)
    └─ extracts types from handler's effect parameter annotation
    └─ no `types` kwarg exposed to users

Layer 2 — Python-Rust bridge:
    vm.WithHandler(handler, expr, types=extracted_types, return_clause=...)
    └─ passes extracted types to the Rust VM

Layer 3 — Rust IR:
    DoCtrl::WithHandler { handler, body, types: Option<Vec<PyShared>>, return_clause }
    └─ VM performs isinstance pre-filter before dispatching to handler
```

### Type extraction rules

The `WithHandler` Python wrapper inspects the handler's first parameter (`effect`) type annotation and extracts concrete effect types:

| Annotation | Extracted `types` | VM behavior |
|------------|-------------------|-------------|
| `effect: Tell` | `(Tell,)` | Only dispatch for `Tell` instances |
| `effect: Tell \| Ask \| Get` | `(Tell, Ask, Get)` | Dispatch for any of the three |
| `effect: Effect` | `None` | Dispatch for all effects (base class = no filter) |
| `effect: EffectBase` | `None` | Same — base class means "all" |
| `effect: Any` | `None` | No annotation constraint = no filter |
| `effect` (no annotation) | `None` | No annotation = no filter |
| `effect: MyCustomEffect` | `(MyCustomEffect,)` | Dispatch for `MyCustomEffect` and subclasses |

**Key rules:**
1. `Effect` and `EffectBase` annotations mean "all effects" → `types=None` (no filter).
2. Union types (`A | B | C` or `Union[A, B, C]`) are decomposed into a tuple.
3. Single concrete types produce a single-element tuple.
4. Missing or `Any` annotation → no filter (backward compatible).

### isinstance semantics

The VM's type check uses `isinstance(effect, handler.types)`, which inherently supports subclass relationships:

```python
class WriterEffect(Effect): ...
class Tell(WriterEffect): ...
class StructuredLog(WriterEffect): ...

@do
def writer_handler(effect: WriterEffect, k):
    # VM filter: isinstance(effect, (WriterEffect,))
    # Matches Tell, StructuredLog, and any future WriterEffect subclass
    ...
```

This is the same semantic as what handlers already do manually in their body. The VM is hoisting the isinstance check from Python into the dispatch loop.

### Interaction with `yield Pass()`

Type filtering is a **pre-screen**, not a contract. A handler whose annotation matches can still `yield Pass()` based on runtime values:

```python
@do
def seedream_handler(effect: ImageGenerate, k):
    # VM pre-filters: only called for ImageGenerate instances
    if not _is_seedream_model(effect.model):
        yield Pass()  # ← still valid: "I handle ImageGenerate, but not this model"
        return
    value = yield _generate(effect)
    return (yield Resume(k, value))
```

The annotation says "I am *interested* in `ImageGenerate`", not "I will definitely handle every `ImageGenerate`".

### Backward compatibility

- **No API change**: `WithHandler(handler, expr)` signature is unchanged.
- **No behavioral change**: Handlers with `effect: Effect` or no annotation behave exactly as today.
- **Existing handlers gain automatic optimization**: Any handler with specific type annotations (which all well-written handlers already have) automatically benefits.

---

## Rust IR changes

### `DoCtrl::WithHandler`

```rust
// Before
WithHandler {
    handler: KleisliRef,
    body: Box<DoCtrl>,
    return_clause: Option<PyShared>,
}

// After
WithHandler {
    handler: KleisliRef,
    body: Box<DoCtrl>,
    types: Option<Vec<PyShared>>,   // NEW: extracted from annotation
    return_clause: Option<PyShared>,
}
```

`types: None` = no filter (dispatch all effects to this handler). `types: Some(vec)` = VM performs isinstance check before dispatching.

### Dispatch logic (pseudocode)

```rust
fn should_dispatch_to_handler(effect: &PyAny, handler: &InstalledHandler) -> bool {
    match &handler.types {
        None => true,  // no filter, handle all
        Some(types) => {
            // isinstance(effect, tuple(types))
            Python::attach(|py| {
                let type_tuple = PyTuple::new(py, types);
                effect.is_instance(type_tuple).unwrap_or(false)
            })
        }
    }
}
```

**Note**: `isinstance` requires the GIL. This is acceptable for v1 — the optimization eliminates the far more expensive Python function call overhead. Future versions may add pointer-identity fast paths or effect type tags for GIL-free dispatch.

---

## Python wrapper changes

### `doeff/rust_vm.py`

```python
def WithHandler(handler, expr, return_clause=None):
    handler = _coerce_handler(handler, api_name="WithHandler", role="handler")
    types = _extract_handler_effect_types(handler)
    vm = _vm()
    return vm.WithHandler(handler, expr, types=types, return_clause=return_clause)
```

### Type extraction implementation

```python
def _extract_handler_effect_types(handler) -> tuple[type, ...] | None:
    """Extract effect types from handler's first parameter annotation.

    Returns None if the handler accepts all effects (Effect, EffectBase, Any, or no annotation).
    Returns a tuple of concrete types for union annotations.
    """
    func = getattr(handler, "func", handler)
    hints = _safe_get_type_hints(func)
    sig = _safe_signature(func)
    if sig is None:
        return None

    # Get first parameter (the effect parameter)
    params = list(sig.parameters.values())
    if not params:
        return None

    effect_param = params[0]
    annotation = hints.get(effect_param.name, effect_param.annotation)

    if annotation is inspect._empty:
        return None

    return _resolve_effect_types(annotation)


def _resolve_effect_types(annotation) -> tuple[type, ...] | None:
    """Resolve an annotation to a tuple of concrete effect types, or None for 'all'."""
    from doeff.types import Effect, EffectBase

    # Base classes mean "all effects"
    if annotation in (Effect, EffectBase, Any):
        return None

    # Concrete effect type
    if isinstance(annotation, type) and issubclass(annotation, EffectBase):
        return (annotation,)

    # Union: Tell | Ask | Get
    origin = get_origin(annotation)
    union_type = getattr(types, "UnionType", None)
    if origin is Union or (union_type is not None and isinstance(annotation, union_type)):
        args = get_args(annotation)
        concrete = []
        for arg in args:
            if arg in (Effect, EffectBase, Any):
                return None  # union includes base class → handle all
            if isinstance(arg, type) and issubclass(arg, EffectBase):
                concrete.append(arg)
            else:
                return None  # non-type in union → can't filter safely
        return tuple(concrete) if concrete else None

    # Annotated[X, ...] → unwrap
    if origin is Annotated:
        inner_args = get_args(annotation)
        if inner_args:
            return _resolve_effect_types(inner_args[0])

    # Anything else we don't understand → no filter (safe default)
    return None
```

---

## Testing strategy

### Unit tests

1. **Type extraction**:
   - `effect: Tell` → `(Tell,)`
   - `effect: Tell | Ask` → `(Tell, Ask)`
   - `effect: Effect` → `None`
   - `effect: Any` → `None`
   - No annotation → `None`
   - `effect: WriterEffect` (base class with subclasses) → `(WriterEffect,)`

2. **Dispatch behavior**:
   - Handler with `effect: Tell` is NOT called for `Ask` effects
   - Handler with `effect: Tell` IS called for `Tell` subclass effects
   - Handler with `effect: Effect` is called for all effects (backward compat)
   - Handler with `effect: Tell | Ask` is called for both, not for `Get`

3. **Pass() still works**:
   - Handler with `effect: ImageGenerate` can still `yield Pass()` after runtime check
   - Effect continues to next handler in chain

### Integration tests

4. **Stacked handlers**: Multiple handlers with different type annotations in a `WithHandler` chain dispatch correctly.
5. **Performance**: Measure dispatch overhead reduction for handlers with narrow type annotations.

---

## Implementation plan

| Phase | Scope | Files |
|-------|-------|-------|
| 1 | Python type extraction | `doeff/rust_vm.py` — add `_extract_handler_effect_types` |
| 2 | Rust IR extension | `do_ctrl.rs` — add `types` field to `WithHandler` |
| 3 | PyO3 bridge | `pyvm.rs` — accept `types` kwarg on `WithHandler` |
| 4 | VM dispatch | `vm.rs` — isinstance pre-check in handler dispatch loop |
| 5 | Tests | New test file or extend `tests/test_with_handler.py` |
| 6 | Semgrep guard | Optional: rule encouraging type annotations on handlers |

### Estimated complexity

Phases 2-4 mirror the WithIntercept type filtering (VM-DEBT-008, PR #194). The Rust changes follow the same pattern: `types: Option<Vec<PyShared>>` field, isinstance check before dispatch. Phase 1 (type extraction) is new Python-side logic but straightforward given `_is_effect_annotation_kind` already handles the annotation parsing.

---

## Future optimizations (out of scope for v1)

- **Pointer-identity fast path**: Cache `id(type)` at registration, compare `id(type(effect))` before falling back to isinstance. Avoids Python call overhead for exact type matches.
- **Effect type tags**: Assign monotonic integer tags to effect classes at definition time. Pure integer comparison, zero Python object access.
- **Rust-native type registry**: Map Python type objects to Rust-side type IDs at handler registration. Fully GIL-free dispatch.

These are implementation details invisible to users. The isinstance semantic contract established in v1 remains the invariant.
