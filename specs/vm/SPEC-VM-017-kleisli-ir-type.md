# SPEC-VM-017: Kleisli Arrow as IR-Level Callable

## Status: Draft (Revision 1)

### Motivation

The doeff IR currently has no first-class callable type. Python callables leak into the IR
as opaque `Py<PyAny>` values (`Value::PythonHandlerCallable`, `Value::Python` in Map/FlatMap).
Rust callables are embedded as `Value::RustProgramInvocation` with arguments pre-bundled.
This causes:

1. **`@do` handlers don't work with `WithHandler`** — `@do` functions return `DoExpr` when
   called, but the handler dispatch path (`Expand`) expects a generator.
2. **No IR-level callable concept** — the VM has special-case FFI code for each callable
   variant (`PythonHandlerCallable`, `RustProgramInvocation`, plain `Python`).
3. **Semantic confusion** — `Apply` vs `Expand` vs `FlatMap.binder` all express the same
   concept (call something, get DoExpr, evaluate it) through different mechanisms.

### Core Insight

A handler `f(effect, k) → DoExpr` is a **Kleisli arrow**: `T → M U` (value to computation).
This is the same concept as `FlatMap.binder: A → DoExpr[B]`.

SPEC-008 R16-B already identified: `Expand(f, args) = Eval(Apply(f, args))` — "program
invocation is macro expansion." R16-C named `@do` generators "lazy macros." The missing
piece: a **first-class IR Value** for this callable type.

doeff already calls `@do`'s return type `KleisliProgram` on the Python side. This spec
introduces the IR-level equivalent.

---

## §1 Kleisli Arrow Type

### R1-A: `Kleisli` as a Rust trait

```rust
/// IR-level callable: T → DoExpr[U]
///
/// A Kleisli arrow takes arguments and produces a DoExpr (computation)
/// that the VM evaluates. This is the IR's concept of a "function into
/// computations" — the same concept as FlatMap's binder.
pub trait Kleisli: Debug + Send + Sync {
    /// Apply the arrow to arguments, producing a DoExpr to evaluate.
    fn apply(&self, py: Python<'_>, args: Vec<Value>) -> Result<DoCtrl, VMError>;

    /// Debug metadata for tracing/error reporting.
    fn debug_info(&self) -> KleisliDebugInfo;

    /// Optional Python identity for handler self-exclusion (OCaml semantics).
    fn py_identity(&self) -> Option<PyShared> {
        None
    }
}

pub type KleisliRef = Arc<dyn Kleisli>;
```

### R1-B: `PyKleisli` — Python-backed implementation

```rust
/// Wraps a Python callable as a Kleisli arrow.
///
/// When applied:
/// - If the callable returns a DoExpr → return it directly
/// - If the callable returns a generator → wrap as DoCtrl::ASTStream (IRStream)
/// - Both produce DoExpr, which the VM evaluates uniformly.
pub struct PyKleisli {
    callable: Py<PyAny>,
    metadata: KleisliDebugInfo,
}

impl Kleisli for PyKleisli {
    fn apply(&self, py: Python<'_>, args: Vec<Value>) -> Result<DoCtrl, VMError> {
        let py_args = values_to_py_tuple(py, &args)?;
        let result = self.callable.call1(py, py_args)?;

        if is_doexpr(py, &result) {
            // @do function path: result IS a DoExpr
            classify_yielded_bound(py, result.bind(py))
        } else if is_generator(py, &result) {
            // Plain generator path: wrap as IRStream
            let stream = PythonGeneratorStream::new(result, ...);
            Ok(DoCtrl::ASTStream { stream: Arc::new(Mutex::new(Box::new(stream))), metadata: ... })
        } else {
            Err(VMError::type_error("Kleisli must return DoExpr or generator"))
        }
    }
}
```

### R1-C: `RustKleisli` — Rust-backed implementation

```rust
/// Wraps a Rust ASTStreamFactory as a Kleisli arrow.
///
/// When applied, creates a Rust ASTStreamProgram and returns it as
/// DoCtrl::ASTStream. Replaces the current RustProgramInvocation pattern
/// where factory+effect+continuation are bundled together.
pub struct RustKleisli {
    factory: ASTStreamFactoryRef,
    metadata: KleisliDebugInfo,
}

impl Kleisli for RustKleisli {
    fn apply(&self, py: Python<'_>, args: Vec<Value>) -> Result<DoCtrl, VMError> {
        // args[0] = effect, args[1] = continuation (for handler Kleislis)
        let effect = extract_effect(&args[0])?;
        let continuation = extract_continuation(&args[1])?;
        let program = self.factory.create_program();
        // Start the program and return as ASTStream
        ...
    }
}
```

### R1-D: `Value::Kleisli` variant

```rust
enum Value {
    // ... existing variants ...
    Kleisli(KleisliRef),   // IR-level callable: args → DoExpr
}
```

Replaces:
- `Value::PythonHandlerCallable(Py<PyAny>)` → `Value::Kleisli(Arc<PyKleisli>)`
- `Value::RustProgramInvocation(...)` → `Value::Kleisli(Arc<RustKleisli>)`

---

## §2 WithHandler Changes

### R1-E: Handler field becomes DoExpr

```rust
// Before:
WithHandler {
    handler: Handler,              // Arc<dyn HandlerInvoke> — Rust trait object
    expr: Py<PyAny>,
    return_clause: Option<PyShared>,
    py_identity: Option<PyShared>,
}

// After:
WithHandler {
    handler_expr: Py<PyAny>,       // DoExpr that evaluates to Value::Kleisli
    body: Py<PyAny>,               // DoExpr — the computation to handle
    return_clause: Option<PyShared>,
}
```

`handler_expr` is a DoExpr. The VM evaluates it once when entering the `WithHandler`
scope, producing a `Value::Kleisli(...)`. The Kleisli value is stored in the
`PromptBoundary` segment for later dispatch.

`py_identity` moves into the Kleisli trait (`Kleisli::py_identity()`).

### R1-F: Two-phase handler evaluation

```
Phase 1 — Scope entry:
    handler: Value::Kleisli = eval(handler_expr)
    store handler in PromptBoundary segment
    begin evaluating body

Phase 2 — Effect dispatch:
    handler_body: DoCtrl = handler.apply([effect, continuation])
    eval(handler_body)
```

### R1-G: `can_handle` moves to dispatch policy

The current `HandlerInvoke::can_handle()` gates which effects a handler processes.
`PythonHandler::can_handle()` always returns `true`. This concept is SEPARATE from
Kleisli — it belongs to handler dispatch policy, not call mechanics.

Options (to be decided in implementation):
- A. Attach to the Kleisli trait as an optional method (default: accept all)
- B. Separate `HandlerMatcher` type stored alongside the Kleisli in PromptBoundary
- C. Python handlers always accept all; Rust handlers carry a predicate on the factory

---

## §3 WithIntercept Changes

### R1-H: Interceptor field becomes DoExpr

```rust
// Before:
WithIntercept {
    interceptor: PyShared,          // opaque Python callable
    expr: Py<PyAny>,
    metadata: Option<CallMetadata>,
}

// After:
WithIntercept {
    interceptor_expr: Py<PyAny>,    // DoExpr that evaluates to a callable value
    body: Py<PyAny>,                // DoExpr
    metadata: Option<CallMetadata>,
}
```

Note: WithIntercept's `f(effect) → effect` is a pure function (not Kleisli) — it returns
a value, not a computation. The interceptor callable evaluates to a regular `Value::Python`
callable, not `Value::Kleisli`. The interceptor protocol (`Apply`, not `Expand`) is unchanged.

If future requirements need interceptors to produce computations, they would then become
Kleisli arrows. But current semantics are synchronous value-returning.

---

## §4 `@do` Function Integration

### R1-I: `@do` functions as Kleisli arrows

A `@do` decorated function returns `KleisliProgram` (Python-side). When used as a handler:

1. Python wrapper (`doeff/rust_vm.py`) wraps it in `PyKleisli`
2. `handler_expr = Pure(Value::Kleisli(PyKleisli(kleisli_program)))`
3. VM evaluates `handler_expr` → `Value::Kleisli(PyKleisli(...))`
4. On dispatch: `PyKleisli.apply([effect, k])` calls the KleisliProgram
5. KleisliProgram returns DoExpr → VM evaluates it

Plain generator functions follow the same path — `PyKleisli.apply()` detects the generator
return and wraps it as `DoCtrl::ASTStream`.

### R1-J: Annotation gate relaxed

The current `validate_do_handler_effect_annotation` rejects `@do` handlers without
`Effect` annotation on the first parameter. This gate should be preserved for type safety
but should no longer cause runtime `TypeError` — the Kleisli wrapping handles both `@do`
and plain handlers uniformly.

---

## §5 Expand / Apply Unification

### R1-K: `Expand` as Kleisli application

SPEC-008 R16-B already established: `Expand(f, args) = Eval(Apply(f, args))`.

With `Value::Kleisli`, this becomes concrete:
- `Expand(factory, args)` where `factory` is `Value::Kleisli(...)` →
  call `kleisli.apply(args)` → get DoCtrl → eval
- `Apply(f, args, evaluate_result=true)` → same semantics

Long-term, `Expand` can be lowered to `Apply(kleisli, args, evaluate_result=true)` or
removed entirely. Short-term, both can coexist for backward compatibility.

### R1-L: FlatMap binder as Kleisli

`FlatMap.binder` is semantically a Kleisli arrow: `A → DoExpr[B]`. Long-term, the binder
field should be `Value::Kleisli` instead of opaque `PyShared`. This is a later migration
phase — FlatMap works today and isn't blocking `@do` handler support.

---

## §6 Deprecated Concepts

| Deprecated | Replaced by |
|-----------|-------------|
| `Value::PythonHandlerCallable(Py<PyAny>)` | `Value::Kleisli(Arc<PyKleisli>)` |
| `Value::RustProgramInvocation(...)` | `Value::Kleisli(Arc<RustKleisli>)` |
| `Handler = Arc<dyn HandlerInvoke>` | `KleisliRef = Arc<dyn Kleisli>` |
| `PythonHandler` struct | `PyKleisli` struct |
| `HandlerInvoke` trait | `Kleisli` trait |
| `DoeffGeneratorFn` (as handler wrapper) | `PyKleisli` (wraps any Python callable) |

---

## §7 Migration Plan

### Phase 1: Foundation
1. Add characterization tests for current handler dispatch behavior
2. Define `Kleisli` trait, `PyKleisli`, `RustKleisli` types
3. Add `Value::Kleisli(KleisliRef)` variant
4. VM `handle_yield_apply`/`handle_yield_expand` accept `Value::Kleisli`

### Phase 2: Handler migration
5. `PythonHandler` delegates to `PyKleisli` internally
6. `WithHandler` Python wrapper coerces handlers to `PyKleisli`
7. `@do` handlers work via `PyKleisli` path

### Phase 3: WithHandler IR change
8. `WithHandler.handler` field changes from `Handler` to `DoExpr`
9. VM implements two-phase evaluation (eval handler_expr → Kleisli, then dispatch)
10. `handler_expr = Pure(Value::Kleisli(...))` is the common case

### Phase 4: Cleanup
11. Remove `Value::PythonHandlerCallable`
12. Remove `Value::RustProgramInvocation`
13. Remove `HandlerInvoke` trait and `Handler` type alias
14. Optionally: `FlatMap.binder` → `Value::Kleisli`, `Expand` lowered to Apply+Eval

---

## §8 Naming Conventions

| IR concept | Rust type | Python type |
|-----------|-----------|-------------|
| Kleisli arrow (trait) | `Kleisli` | — |
| Python-backed Kleisli | `PyKleisli` | wraps `KleisliProgram` or plain callable |
| Rust-backed Kleisli | `RustKleisli` | — |
| Kleisli as Value | `Value::Kleisli(KleisliRef)` | — |
| Reference type | `KleisliRef = Arc<dyn Kleisli>` | — |

### Naming rationale

- `Kleisli` aligns with existing `KleisliProgram` naming on the Python side
- Standard PL terminology: Kleisli arrow = `A → M B` (value to computation)
- Distinguishes from closures (`A → B`, value to value) which are Map's domain
- `FlatMap` IS Kleisli composition — the concepts are unified, not invented

---

## §9 Open Questions

1. **`can_handle` placement**: Should `Kleisli` trait include `can_handle()`, or should
   it be a separate `HandlerMatcher` stored in `PromptBoundary`? Python handlers always
   accept all effects; Rust handlers use predicates.

2. **`return_clause`**: Currently a Python callable `x → value`. Should it also become a
   Kleisli arrow (`x → DoExpr`) so return clauses can perform effects? Current implementation
   treats it as a plain `Apply` (value-returning).

3. **IRStream naming**: `ASTStream` should be renamed to `IRStream` to match the actual
   semantics (it produces IR/DoCtrl nodes, not AST). This is a separate cleanup.

4. **CallArg naming**: `CallArg` should be renamed to `ValueOrExpr` to reflect its actual
   semantics (either an already-resolved Value or an unevaluated DoExpr).
