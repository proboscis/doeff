# SPEC-KPC-001: KleisliProgramCall — Call-Time Macro Expansion

## Status: WIP Discussion Draft (Rev 1)

**Authoritative specification for KPC under the macro model (doeff-13).**

This spec describes how `KleisliProgram.__call__()` operates as a call-time macro
that emits a `Call` DoCtrl directly. KPC is **not** an effect. It does not extend
`PyEffectBase`. It is not `EffectValue`. There is no `Perform(KPC)`. The KPC handler
(`KpcHandlerFactory`, `KpcHandlerProgram`, `ConcurrentKpcHandlerProgram`) is removed.

**Supersedes**: SPEC-TYPES-001 sections 3, 4.6, 5.6 (KPC-specific content);
SPEC-008 R11-A KPC parts; SPEC-009 R6-C/R6-D KPC dispatch and kpc handler section.

---

## 1. Overview

`KleisliProgramCall` (KPC) is a **call-time macro**. When a user calls a `@do`-decorated
function (a `KleisliProgram`), the `__call__()` method performs a pure transformation
from the call-site arguments into a `Call` DoCtrl — a DoExpr node that the VM evaluates
directly. No intermediate KPC runtime type is constructed. No handler dispatch occurs.
No `Perform(KPC(...))` is emitted.

The `Call` DoCtrl IS a `DoExpr`, giving users full composability:
- `.map(f)` produces `Map(Call(...), f)`
- `.flat_map(f)` produces `FlatMap(Call(...), f)`
- `yield` in a `@do` body sends the `Call` to the VM
- `run()` evaluates it directly

**Key invariants:**
- KPC does NOT extend `PyEffectBase`.
- KPC is NOT an `EffectValue`.
- `classify_yielded` never encounters a KPC type — users yield the `Call` DoCtrl
  that `__call__()` produced, caught by the existing `DoCtrlBase` isinstance check.
- `default_handlers()` does NOT include a KPC handler. No KPC handler exists.

See doeff-13 for the full rationale and discussion.

---

## 2. Macro Expansion Semantics

When a `KleisliProgram` is called — e.g., `fetch_user(Ask("key"))` — the following
steps execute synchronously at Python call time (not at VM evaluation time):

1. **Retrieve cached auto-unwrap strategy** from the `KleisliProgram` instance
   (computed once at decoration time; see section 6).
2. **Classify each argument** according to the strategy's `should_unwrap` decision
   for that parameter position/name (see section 3).
3. **Build DoExpr argument list**: for each argument:
   - If `should_unwrap=True` and the argument is an `EffectBase` instance:
     emit `Perform(arg)` (the VM will dispatch the effect and resolve to a value).
   - If `should_unwrap=True` and the argument is a `DoCtrlBase` instance:
     emit the DoCtrl directly (the VM will evaluate it).
   - If `should_unwrap=True` and the argument is a plain value:
     emit `Pure(arg)`.
   - If `should_unwrap=False`: emit `Pure(arg)` (pass the object as-is,
     even if it is an Effect or DoCtrl).
4. **Construct `CallMetadata`** with the caller's identity (see section 4).
5. **Return** `Call(Pure(execution_kernel), [DoExpr args], kwargs, metadata)`.

The result is a `Call` DoCtrl — a DoExpr node. No side effects occur. No handler
stack is consulted. The transformation is pure.

### Example

```python
@do
def fetch_user(id: int) -> Program[dict]:
    url = yield Ask("db")
    return db.get(url, id)

fetch_user(Ask("key"))
# Macro expansion at call time:
#   1. Inspect annotations: id is int -> should_unwrap=True
#   2. Ask("key") is EffectBase + should_unwrap -> Perform(Ask("key"))
#   3. Emit: Call(Pure(kernel), [Perform(Ask("key"))], {}, metadata)
#
# VM evaluation at runtime:
#   1. Eval arg[0]: dispatch Ask -> get "db_url"
#   2. Call kernel("db_url") -> generator
#   3. Push generator as frame, step it
```

---

## 3. Auto-Unwrap Classification Rules

The auto-unwrap strategy determines which arguments to a `KleisliProgram` call are
resolved by the VM (unwrapped) vs passed as-is. The rules are unchanged from the
original design — only the execution context changed: rules are applied at
`KleisliProgram.__call__()` time (call-time macro), not at handler dispatch time.

### 3.1 Annotation-aware classification

The strategy MUST respect type annotations to decide which args to unwrap.
This is critical for enabling `@do` functions that transform programs:

```python
@do
def run_both(a: int, b: int) -> Program[tuple]:
    return (a, b)
# a and b are auto-unwrapped — plain type annotations

@do
def transform_program(p: Program[int]) -> Program[int]:
    val = yield p  # user manually yields the program
    return val * 2
# p is NOT unwrapped — annotated as Program[T]

@do
def inspect_effect(e: Effect) -> Program[str]:
    return type(e).__name__
# e is NOT unwrapped — annotated as Effect
```

### 3.2 Classification rules

**DO unwrap** (`should_unwrap = True`) when annotation is:
- Plain types: `int`, `str`, `dict`, `User`, etc.
- No annotation (default: unwrap)
- Any type that is NOT a Program/Effect family type

**DO NOT unwrap** (`should_unwrap = False`) when annotation is:
- `Program`, `Program[T]`
- `DoCtrl`, `DoCtrl[T]`
- `Effect`, `Effect[T]`
- `DoExpr`, `DoExpr[T]`
- Any subclass of `Effect` (e.g., custom effect types)
- Any subclass of `DoCtrl`
- `Optional[Program[T]]`, `Program[T] | None`, `Annotated[Program[T], ...]`

**String annotation handling** (for `from __future__ import annotations`):
- Supports quoted strings, `Optional[...]`, `Annotated[...]`, union `|`
- Matches normalized strings: `"Program"`, `"Program[...]"`, `"DoCtrl"`,
  `"DoCtrl[...]"`, `"Effect"`, `"Effect[...]"`, `"DoExpr"`, etc.

### 3.3 Parameter kinds

- `POSITIONAL_ONLY`: indexed in `strategy.positional`
- `POSITIONAL_OR_KEYWORD`: indexed in both `strategy.positional` and `strategy.keyword`
- `KEYWORD_ONLY`: in `strategy.keyword`
- `VAR_POSITIONAL` (`*args`): single `strategy.var_positional` bool for all
- `VAR_KEYWORD` (`**kwargs`): single `strategy.var_keyword` bool for all

### 3.4 Arg treatment by should_unwrap and value type

| Arg value | `should_unwrap` | Macro action |
|-----------|----------------|--------------|
| `EffectBase` instance | `True` | Emit `Perform(arg)` — VM dispatches effect, resolves to value |
| `DoCtrlBase` instance | `True` | Emit the DoCtrl directly — VM evaluates it |
| Plain value (`int`, `str`, etc.) | either | Emit `Pure(value)` |
| `EffectBase` instance | `False` | Emit `Pure(arg)` — pass effect object as-is |
| `DoCtrlBase` instance | `False` | Emit `Pure(arg)` — pass DoCtrl object as-is |

All args in the resulting `Call` are DoExpr nodes. The VM evaluates each arg
sequentially left-to-right before invoking the kernel.

---

## 4. Metadata Population

`CallMetadata` is populated by `KleisliProgram.__call__()` at call time, as part
of the macro expansion. This replaces the previous model where the KPC handler
extracted metadata from the `KleisliProgramCall` effect at dispatch time.

### 4.1 CallMetadata fields

| Field | Type | Source | Purpose |
|-------|------|--------|---------|
| `function_name` | `str` | `KleisliProgram.__name__` | Human-readable name for tracing |
| `source_file` | `str` | `original_func.__code__.co_filename` | Source file where `@do` function is defined |
| `source_line` | `int` | `original_func.__code__.co_firstlineno` | Line number in source file |
| `program_call` | `Optional[object]` | Reference to call context | Optional rich introspection (args, kwargs) |

### 4.2 Population pseudo-code (macro model)

```python
# Inside KleisliProgram.__call__(self, *args, **kwargs):
metadata = CallMetadata(
    function_name=self.__name__,
    source_file=self.original_func.__code__.co_filename,
    source_line=self.original_func.__code__.co_firstlineno,
    program_call=None,  # or a lightweight call descriptor
)

# Build DoExpr args using cached strategy (section 3)
do_expr_args = self._build_call_args(args, kwargs)

return Call(
    f=Pure(self.execution_kernel),
    args=do_expr_args,
    kwargs=do_expr_kwargs,
    metadata=metadata,
)
```

Metadata extraction no longer requires GIL-separated driver/handler interaction.
The `KleisliProgram` instance has direct access to all metadata fields at call
time — no downcast, no `PyRef<PyKPC>`, no handler dispatch path.

---

## 5. Return Type and Composability

`KleisliProgram.__call__()` returns a `Call` DoCtrl directly. The `Call` IS a `DoExpr`.

### 5.1 What __call__ returns

```
KleisliProgram.__call__(*args, **kwargs) -> Call[T]
```

Where `Call[T]` is a `DoCtrl[T]`, which is a `DoExpr[T]` (= `Program[T]`).

### 5.2 Composability

Because the return value is a `DoExpr`, users get full composability:

```python
result = fetch_user(42)                    # Call DoCtrl
mapped = fetch_user(42).map(lambda u: u.name)  # Map(Call(...), f)
chained = fetch_user(42).flat_map(enrich)  # FlatMap(Call(...), f)
value = yield fetch_user(42)               # yield sends Call to VM
```

Every intermediate value is a DoExpr — always yieldable, always composable.

### 5.3 KleisliProgramCall as standalone type

Under the macro model, `KleisliProgramCall` as a standalone `#[pyclass]` type
**may be eliminated entirely**. The metadata that was carried on KPC now lives
in `CallMetadata` on the `Call` DoCtrl. The args and kwargs are embedded in the
`Call` node as DoExpr sub-expressions. There is no need for a separate runtime
type that bundles these fields.

If a lightweight call descriptor is needed for introspection (e.g., the
`program_call` field in `CallMetadata`), it can be a plain data object — it
does NOT need to extend `PyEffectBase` or participate in handler dispatch.

---

## 6. Strategy Caching

The auto-unwrap strategy is computed **once** at decoration time, not per-call.

```python
@do  # <-- decoration time: _build_auto_unwrap_strategy runs HERE
def fetch_user(id: int) -> Program[dict]:
    ...

# Every call to fetch_user() reuses the cached strategy:
fetch_user(Ask("key"))   # uses cached strategy
fetch_user(Pure(42))     # uses cached strategy
```

The strategy is cached on the `KleisliProgram` instance as an attribute
(e.g., `self._auto_unwrap_strategy`). The `_build_auto_unwrap_strategy`
function inspects annotations from `kleisli_source` (the original function)
and produces the strategy struct. This runs once per decorated function, not
once per call.

**No per-call computation overhead.** The macro expansion at call time only
reads the cached strategy to classify arguments — it does not recompute it.

---

## 7. Why KPC is a Macro, Not an Effect

KPC resolution is **compilation** (object to DoExpr tree), not **runtime dispatch**
(effect to handler). These are different phases that must not share the same
dispatch mechanism.

### 7.1 The fatal flaw of KPC-as-effect

When a `@do` function is used as a handler, the handler call produces a KPC.
The KPC handler's auto-unwrap evaluates the effect arg, which re-dispatches to
the same handler, creating infinite recursion:

```
handler(eff, k) -> KPC -> KPC handler -> auto-unwrap -> Eval(eff) ->
dispatch eff -> same handler -> KPC -> KPC handler -> ...  (infinite)
```

This is inherent in the combination of:
- KPC-as-effect
- auto-unwrap
- `@do`-on-handlers
- handler protocol

No fix exists without special-casing KPC or restricting `@do` usage. See doeff-13.

### 7.2 Macro vs dispatch — phase separation

| Phase | Mechanism | Side effects | Handler stack |
|-------|-----------|-------------|---------------|
| Compilation (macro) | object to DoExpr | None | Not involved |
| Runtime (dispatch) | effect to handler | Handler-defined | Walked |

A macro is a **pure transformation** from a user-space object to a DoExpr tree.
No side effects, no handler stack, no dispatch. KPC is the first (and currently
only) macro. The concept may be generalized later (`DefMacro` pyclass) but is
not needed now.

### 7.3 Pluggable resolution strategies — removed

The previous design allowed swapping KPC handlers for different resolution
strategies (parallel, cached, mocked). This extensibility point is removed
under the macro model. If needed in the future, pluggable resolution can be
provided via a VM-level `Call` arg evaluation strategy, not via handler dispatch.

---

## 8. What Changed (Historical)

This section summarizes changes from the old KPC-as-effect model to the current
macro model. Historical terminology is preserved for audit trail.

### 8.1 Old model vs new model

| Aspect | Old Model (pre-Rev 12) | New Model (Rev 12, doeff-13) |
|--------|----------------------|----------------------------|
| KPC type identity | `#[pyclass(frozen, extends=PyEffectBase)]` | Not an effect. `__call__()` returns `Call` DoCtrl |
| What `__call__()` returns | `KleisliProgramCall` (EffectValue) | `Call` DoCtrl directly |
| How KPC is dispatched | `Perform(KPC(...))` through handler stack | No dispatch. VM evaluates the `Call` DoCtrl |
| Who resolves args | KPC handler (`KpcHandlerProgram`) | Macro expansion at call time + VM eval |
| Auto-unwrap strategy | Computed by handler at dispatch time | Computed at decoration time, cached, applied at call time |
| KPC handler in `default_handlers()` | Yes (`kpc` sentinel included) | No. KPC handler removed entirely |
| `classify_yielded` KPC path | EffectBase isinstance catches KPC | KPC never reaches classifier. `Call` is DoCtrlBase |
| Concurrent resolution | `ConcurrentKpcHandlerProgram` variant | Removed. Future: VM-level Call arg strategy |

### 8.2 Removed components

The following components are removed under the macro model:
- `KpcHandlerFactory` — factory for creating KPC handlers
- `KpcHandlerProgram` — sequential resolution KPC handler
- `ConcurrentKpcHandlerProgram` — concurrent resolution variant
- `kpc` sentinel in `default_handlers()` — no handler needed
- `Perform(KPC(...))` dispatch path — KPC is never dispatched as an effect

### 8.3 Historical references

- **SPEC-TYPES-001 Rev 9**: Introduced KPC as `#[pyclass(frozen, extends=PyEffectBase)]`.
  Auto-unwrap strategy moved to handler. **Superseded by Rev 12.**
- **SPEC-TYPES-001 section 3**: Described the KPC handler architecture.
  **Superseded by this spec (SPEC-KPC-001).**
- **SPEC-TYPES-001 section 4.6**: Described KPC metadata fields on `PyKPC`.
  **Superseded by SPEC-KPC-001 section 4.**
- **SPEC-TYPES-001 section 5.6**: Described how KPC handler populates metadata.
  **Superseded by SPEC-KPC-001 section 4.**

---

## 9. Open Questions

1. **Auto-unwrap at call time for unannotated `EffectBase` args.**

   When `fetch_user(Ask("key"))` is called and `id` is unannotated, should
   `Ask("key")` become `Perform(Ask("key"))` (auto-resolved by VM) or
   `Pure(Ask("key"))` (passed as-is)?

   The former preserves current behavior but risks the same infinite recursion
   cycle if the `Call` arg evaluation dispatches to a `@do` handler. The latter
   is safe but changes existing `@do` semantics where unannotated args default
   to auto-unwrap.

   Current default: `should_unwrap=True` for unannotated args. This matches
   pre-Rev 12 behavior but the recursion risk under certain handler configurations
   needs further analysis.

   See doeff-13 for full discussion.

---

## 10. References

- **SPEC-TYPES-001** (`specs/core/SPEC-TYPES-001-program-effect-separation.md`):
  Section 1.3 (KPC macro description), section 1.4 (why macro not effect).
  Rev 12 changelog (authoritative KPC model summary).
- **SPEC-008** (`specs/vm/SPEC-008-rust-vm.md`): R15-A (KPC model change).
  `CallMetadata` struct definition. `Call` DoCtrl evaluation semantics.
- **SPEC-009** (`specs/vm/SPEC-009-rust-vm-migration.md`): R9-A (KPC model change).
  `default_handlers()` no longer includes `kpc`.
- **doeff-13**: GitHub issue — `@do` handler KPC infinite recursion.
  Root cause analysis and decision to adopt macro model.
