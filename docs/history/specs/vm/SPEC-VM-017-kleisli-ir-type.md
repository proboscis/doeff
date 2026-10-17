# SPEC-VM-017: Kleisli Arrow as IR-Level Callable

## Status: Draft (Revision 2)

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
    fn apply(&self, py: Python<'_>, args: Vec<Value>) -> Result<DoExpr, VMError>;

    /// Debug metadata for tracing/error reporting.
    fn debug_info(&self) -> KleisliDebugInfo;

    /// Optional Python identity for handler self-exclusion (OCaml semantics).
    fn py_identity(&self) -> Option<PyShared> {
        None
    }
}

pub type KleisliRef = Arc<dyn Kleisli>;
```

### R1-B: `PyKleisli` — Python-backed implementation (`#[pyclass]`)

`PyKleisli` is a **`#[pyclass]`** — a Rust struct exposed to Python via PyO3. This is the
critical design choice: `@do` directly returns a `PyKleisli` instance, and the VM natively
recognizes it as `Value::Kleisli`. No intermediate wrapping layer.

```rust
/// Wraps a Python callable as a Kleisli arrow.
///
/// This is a #[pyclass] — constructible from Python. The @do decorator
/// returns PyKleisli instances directly, making them first-class IR values.
///
/// When applied:
/// - If the callable returns a DoExpr → return it directly
/// - If the callable returns a generator → wrap as DoExpr::ASTStream (IRStream)
/// - Both produce DoExpr, which the VM evaluates uniformly.
#[pyclass]
pub struct PyKleisli {
    callable: Py<PyAny>,
    metadata: KleisliDebugInfo,
}

#[pymethods]
impl PyKleisli {
    #[new]
    fn new(callable: Py<PyAny>, name: String, file: Option<String>, line: Option<u32>) -> Self {
        PyKleisli {
            callable,
            metadata: KleisliDebugInfo { name, file, line },
        }
    }
}

impl Kleisli for PyKleisli {
    fn apply(&self, py: Python<'_>, args: Vec<Value>) -> Result<DoExpr, VMError> {
        let py_args = values_to_py_tuple(py, &args)?;
        let result = self.callable.call1(py, py_args)?;

        if is_doexpr(py, &result) {
            // @do function path: result IS a DoExpr
            classify_yielded_bound(py, result.bind(py))
        } else if is_generator(py, &result) {
            // Plain generator path: wrap as IRStream (ASTStream)
            let stream = PythonGeneratorStream::new(result, ...);
            Ok(DoExpr::ASTStream { stream: Arc::new(Mutex::new(Box::new(stream))), metadata: ... })
        } else {
            Err(VMError::type_error("Kleisli must return DoExpr or generator"))
        }
    }
}
```

**Key property**: Because `PyKleisli` is `#[pyclass]`, `Value::from_pyobject` can detect it:

```rust
// In Value::from_pyobject:
if let Ok(kleisli) = obj.extract::<PyRef<'_, PyKleisli>>() {
    return Value::Kleisli(Arc::new(kleisli.clone()));
}
```

This means `Pure(@do(f))` automatically produces `Pure(Value::Kleisli(...))` — no
explicit conversion needed at the Python wrapper layer.

### R1-C: `RustKleisli` — Rust-backed implementation

```rust
/// Wraps a Rust ASTStreamFactory as a Kleisli arrow.
///
/// When applied, creates a Rust ASTStreamProgram and returns it as
/// DoExpr::ASTStream. Replaces the current RustProgramInvocation pattern
/// where factory+effect+continuation are bundled together.
pub struct RustKleisli {
    factory: ASTStreamFactoryRef,
    metadata: KleisliDebugInfo,
}

impl Kleisli for RustKleisli {
    fn apply(&self, py: Python<'_>, args: Vec<Value>) -> Result<DoExpr, VMError> {
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

### R1-E: Handler field becomes direct `KleisliRef`

```rust
// Before:
WithHandler {
    handler: Handler,              // Arc<dyn HandlerInvoke> — Rust trait object
    expr: Py<PyAny>,               // body as opaque Python object
    return_clause: Option<PyShared>,
    py_identity: Option<PyShared>,
}

// After:
WithHandler {
    handler: KleisliRef,           // direct Kleisli value, already resolved
    body: DoExpr,                  // Rust-native IR expression
    return_clause: Option<PyShared>,
}
```

**Design choice: `f: Kleisli` (direct value), not `f: Expr[Kleisli]`.**

The handler is a direct `KleisliRef`, not a DoExpr that evaluates to one. Rationale:
- Mirrors current design (`handler: Handler` is already a direct value)
- No unnecessary eval step (99% of cases would just be `Pure(Kleisli)`)
- Simpler VM — handler is immediately available at scope entry
- No risk of handler_expr evaluation failing
- No real use case for computed handlers that can't be expressed inside the handler body

The body is `DoExpr` — Rust-native IR, not `Py<PyAny>`. The Python object is converted
to DoExpr at IR construction time (in pyvm.rs `classify_yielded_bound`).

`py_identity` moves into the Kleisli trait (`Kleisli::py_identity()`).

### R1-F: Handler dispatch (single-phase)

```
Scope entry:
    handler: KleisliRef — already resolved, store in PromptBoundary segment
    begin evaluating body (DoExpr)

Effect dispatch:
    handler_body: DoExpr = handler.apply([effect, continuation])
    eval(handler_body)
```

No two-phase evaluation needed. The handler is a direct value.

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

### R1-H: Interceptor field becomes direct `KleisliRef`

Both `WithHandler` and `WithIntercept` use the same interface: `f: KleisliRef` (direct value).

```rust
// Before:
WithIntercept {
    interceptor: PyShared,          // opaque Python callable
    expr: Py<PyAny>,
    metadata: Option<CallMetadata>,
}

// After:
WithIntercept {
    interceptor: KleisliRef,        // direct Kleisli value, already resolved
    body: DoExpr,                   // Rust-native IR expression
    metadata: Option<CallMetadata>,
}
```

**Unified interface**: Both IR nodes take `f: KleisliRef` + `body: DoExpr`.

For interceptors, the Kleisli is `effect → DoExpr[effect]`. In the common (pure transform)
case, `PyKleisli.apply()` returns `Pure(transformed_effect)`. If the interceptor is a `@do`
function that yields effects, the DoExpr is evaluated normally — interceptors gain the
ability to perform computations, not just pure transforms.

**Construction**: `PyKleisli` extracted at IR construction time:

```
Python:  WithIntercept(interceptor=@do(f), expr=body)
                                     ↓
pyvm.rs: extract PyKleisli → KleisliRef, classify body → DoExpr
                                     ↓
WithIntercept { interceptor: kleisli_ref, body: doexpr, ... }
```

---

## §4 `@do` Function Integration

### R1-I: `@do` returns `PyKleisli` directly

Since `PyKleisli` is a `#[pyclass]`, `@do` can return it directly:

```python
# @do decorator (simplified)
def do(func):
    return PyKleisli(
        callable=func,
        name=func.__name__,
        file=inspect.getfile(func),
        line=inspect.getsourcelines(func)[1],
    )
```

The return value is a `PyKleisli` instance — a Rust pyclass that the VM recognizes
natively as `Value::Kleisli`. No intermediate `KleisliProgram` wrapper needed.

### R1-I-2: WithHandler construction chain

```
Python:  WithHandler(handler=@do(my_handler), expr=body)
                              ↓
pyvm.rs: extract PyKleisli (#[pyclass]) → KleisliRef
         classify body → DoExpr
                              ↓
WithHandler { handler: kleisli_ref, body: doexpr, ... }
```

The chain is: `@do(f)` → `PyKleisli(#[pyclass])` → `KleisliRef` → `WithHandler.handler`

No wrapping layer, no `Pure(...)`. The handler is extracted at IR construction time.

### R1-I-3: Plain generator handler backward compatibility

Plain generator handlers (not `@do`) are also wrapped in `PyKleisli` at the Python
wrapper layer (`doeff/rust_vm.py`). `PyKleisli.apply()` detects the generator return
and wraps it as `DoCtrl::ASTStream`. Both paths converge to the same IR.

### R1-J: Annotation gate relaxed

The current `validate_do_handler_effect_annotation` rejects `@do` handlers without
`Effect` annotation on the first parameter. This gate should be preserved for type safety
but should no longer cause runtime `TypeError` — the Kleisli wrapping handles both `@do`
and plain handlers uniformly.

---

## §5 Expand / Apply Unification

### R1-K: `CallArg` removal — Apply/Expand args are `DoExpr`

`CallArg` is a wrapper enum:

```rust
// BEFORE:
enum CallArg {
    Value(Value),      // already resolved
    Expr(PyShared),    // unevaluated Python object
}

Apply {
    f: CallArg,
    args: Vec<CallArg>,
    kwargs: Vec<(String, CallArg)>,
    ...
}
```

This is redundant once everything is DoExpr:
- `CallArg::Value(x)` → `DoExpr::Pure { value: x }`
- `CallArg::Expr(e)` → a DoExpr (the expression itself)

```rust
// AFTER:
Apply {
    f: DoExpr,
    args: Vec<DoExpr>,
    kwargs: Vec<(String, DoExpr)>,
    metadata: CallMetadata,
    evaluate_result: bool,
}

Expand {
    factory: DoExpr,
    args: Vec<DoExpr>,
    kwargs: Vec<(String, DoExpr)>,
    metadata: CallMetadata,
}
```

The VM's arg-resolution loop stays the same — evaluate each DoExpr in sequence, collecting
Values. The fast-path for `DoExpr::Pure` is trivial (return the value immediately).

Impact on `EvalReturnContinuation`:
- `ApplyResolveFunction`, `ApplyResolveArg`, `ApplyResolveKwarg` — change `CallArg` → `DoExpr`
- `ExpandResolveFactory`, `ExpandResolveArg`, `ExpandResolveKwarg` — same
- The resolution logic stays identical, just operating on `DoExpr` instead of `CallArg`

### R1-L: `Expand` as Kleisli application

SPEC-008 R16-B already established: `Expand(f, args) = Eval(Apply(f, args))`.

With `Value::Kleisli`, this becomes concrete:
- `Expand(factory, args)` where `factory` evaluates to `Value::Kleisli(...)` →
  call `kleisli.apply(args)` → get DoExpr → eval
- `Apply(f, args, evaluate_result=true)` → same semantics

Long-term, `Expand` can be lowered to `Apply(kleisli, args, evaluate_result=true)` or
removed entirely. Short-term, both can coexist for backward compatibility.

### R1-M: FlatMap binder as Kleisli

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
| `PythonHandler` struct | `PyKleisli` (`#[pyclass]`) |
| `HandlerInvoke` trait | `Kleisli` trait |
| `DoeffGeneratorFn` (as handler wrapper) | `PyKleisli` (wraps any Python callable) |
| `KleisliProgram` (Python class) | `PyKleisli` (`#[pyclass]`, returned by `@do`) |
| `CallArg` enum | Removed — `Apply`/`Expand` args are `Vec<DoExpr>` |
| `ASTStream` (trait + DoExpr variant) | `IRStream` (produces IR/DoExpr nodes, not AST) |
| `ASTStreamRef` | `IRStreamRef` |
| `ASTStreamStep` | `IRStreamStep` |
| `PythonGeneratorStream` | unchanged (implements `IRStream` instead of `ASTStream`) |

---

## §7 Migration Plan

### Phase 1: Foundation + IR cleanup
1. Rename `ASTStream` → `IRStream`, `ASTStreamRef` → `IRStreamRef`, `ASTStreamStep` → `IRStreamStep`
2. Rename `DoExpr::ASTStream` variant → `DoExpr::IRStream`
3. Rename `ast_stream.rs` → `ir_stream.rs`
4. Remove `CallArg` — Apply/Expand args become `DoExpr` (`Pure(value)` for resolved args)
5. Add characterization tests for current handler dispatch behavior
6. Define `Kleisli` trait, `KleisliDebugInfo`, `KleisliRef`
7. Implement `PyKleisli` as `#[pyclass]` with `impl Kleisli`
8. Implement `RustKleisli` with `impl Kleisli`
9. Add `Value::Kleisli(KleisliRef)` variant
10. `Value::from_pyobject` detects `PyKleisli` → `Value::Kleisli`
11. VM `handle_yield_apply`/`handle_yield_expand` accept `Value::Kleisli`

### Phase 2: `@do` returns `PyKleisli`
8. `@do` decorator returns `PyKleisli` instance (replaces `KleisliProgram`)
9. `PythonHandler` delegates to `PyKleisli` internally
10. Plain generator handlers wrapped in `PyKleisli` at Python wrapper layer
11. `@do` handlers work end-to-end via `PyKleisli` path

### Phase 3: WithHandler + WithIntercept IR change
12. `WithHandler.handler` field → `Expr[Kleisli]` (DoExpr evaluating to Value::Kleisli)
13. `WithIntercept.interceptor` field → `Expr[Kleisli]` (same interface)
14. VM implements two-phase evaluation for both
15. `handler_expr = Pure(Value::Kleisli(@do(f)))` is the common case (auto via from_pyobject)

### Phase 4: Cleanup
16. Remove `Value::PythonHandlerCallable`
17. Remove `Value::RustProgramInvocation`
18. Remove `HandlerInvoke` trait and `Handler` type alias
19. Remove `KleisliProgram` (Python-side, replaced by `PyKleisli` pyclass)
20. Optionally: `FlatMap.binder` → `Value::Kleisli`, `Expand` lowered to Apply+Eval

---

## §8 Naming Conventions

| IR concept | Rust type | Python type |
|-----------|-----------|-------------|
| Kleisli arrow (trait) | `Kleisli` | — |
| Python-backed Kleisli | `PyKleisli` (`#[pyclass]`) | `@do(f)` returns this directly |
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

3. ~~**CallArg naming**~~ — **RESOLVED**: `CallArg` removed entirely (Phase 1). `Apply`/`Expand`
    args are `Vec<DoExpr>`. Already-resolved values use `DoExpr::Pure { value }`.
