# SPEC-VM-018: Core Effects Extraction

## Status: Draft (Revision 1)

## Summary

Extract user-space effect definitions, handler implementations, and the scheduler from the `doeff-vm` crate into a separate `doeff-core-effects` rlib crate. The VM retains only generic stepping machinery and traits. Both crates link into the same `doeff_vm` cdylib (Python extension) to avoid PyO3 `#[pyclass]` identity duplication across `.so` boundaries.

## Motivation

The `doeff-vm` crate currently contains ~7,400 lines of effect/handler-specific code that violates the principle: **the VM must have zero knowledge of any specific effect or handler.** Specific violations:

- `effect.rs` (~600 lines): 24 `#[pyclass]` effect structs + isinstance-based dispatch helpers
- `handler.rs` (~1,600 lines): 6 handler factory+program pairs with hardcoded effect parsing
- `scheduler.rs` (~5,000 lines): Full scheduler handler + task/promise/semaphore management
- `pyvm.rs` (~200 lines): `PyStdlib`, `PySchedulerHandler`, sentinel registration, 23 effect imports
- `vm.rs` (2 lines): `is_execution_context_effect()` hardcoded isinstance — retained as VM-internal (see ADR-1)

This coupling means:
1. Adding a new effect requires modifying the VM crate
2. The VM's compile surface includes all effect/handler code
3. No enforced architectural boundary between "VM mechanism" and "effect semantics"

## Architecture

### Crate Topology

```
packages/
├── doeff-vm/               # cdylib — the Python extension (.so)
│   ├── Cargo.toml          # depends on doeff-vm-core + doeff-core-effects
│   └── src/
│       ├── pyvm.rs         # PyVM wrapper, module init, glue
│       └── lib.rs          # re-exports for Python
│
├── doeff-vm-core/          # NEW rlib — generic VM machinery
│   ├── Cargo.toml          # depends only on pyo3
│   └── src/
│       ├── lib.rs
│       ├── vm.rs           # stepping machine
│       ├── kleisli.rs      # Kleisli trait
│       ├── handler.rs      # IRStreamFactory/IRStreamProgram traits ONLY
│       ├── ir_stream.rs    # IRStream trait
│       ├── effect.rs       # PyEffectBase + DispatchEffect + GetExecutionContext
│       ├── rust_store.rs   # RustStore
│       ├── continuation.rs
│       ├── segment.rs
│       ├── frame.rs
│       ├── dispatch.rs     # DispatchContext
│       ├── ids.rs
│       ├── value.rs
│       ├── error.rs
│       ├── do_ctrl.rs
│       ├── py_shared.rs
│       ├── py_key.rs
│       ├── arena.rs
│       ├── capture.rs
│       ├── doeff_generator.rs
│       ├── python_call.rs
│       ├── driver.rs
│       ├── step.rs
│       ├── debug_state.rs
│       ├── dispatch_state.rs
│       ├── interceptor_state.rs
│       ├── trace_state.rs
│       └── vm_logging.rs
│
└── doeff-core-effects/     # NEW rlib — user-space effects + handlers
    ├── Cargo.toml          # depends on doeff-vm-core + pyo3
    └── src/
        ├── lib.rs          # registration entry point
        ├── effects/
        │   ├── mod.rs
        │   ├── state.rs    # PyGet, PyPut, PyModify
        │   ├── reader.rs   # PyAsk, PyLocal
        │   ├── writer.rs   # PyTell
        │   ├── scheduler.rs # PySpawn, PyGather, PyRace, PyCreatePromise, ...
        │   ├── await_.rs   # PyPythonAsyncioAwaitEffect
        │   ├── result.rs   # PyResultSafeEffect
        │   └── debug.rs    # PyProgramTrace, PyProgramCallStack, PyProgramCallFrame
        ├── handlers/
        │   ├── mod.rs
        │   ├── state.rs    # StateHandlerFactory + StateHandlerProgram
        │   ├── reader.rs   # ReaderHandlerFactory + ReaderHandlerProgram
        │   ├── lazy_ask.rs # LazyAskHandlerFactory + LazyAskHandlerProgram
        │   ├── writer.rs   # WriterHandlerFactory + WriterHandlerProgram
        │   ├── await_.rs   # AwaitHandlerFactory + AwaitHandlerProgram
        │   └── result.rs   # ResultSafeHandlerFactory + ResultSafeHandlerProgram
        ├── scheduler/
        │   ├── mod.rs      # SchedulerHandler + SchedulerState
        │   ├── state.rs    # TaskState, PromiseState, TaskStore, StoreMode
        │   ├── effects.rs  # SchedulerEffect enum + parse_scheduler_python_effect
        │   └── ready.rs    # ReadySet, ReadyEntry, WokenTask, scheduling queue
        └── sentinels.rs    # PyStdlib, PySchedulerHandler, module-level sentinel objects
```

### Dependency Direction (INVARIANT)

```
doeff-core-effects  ──depends──>  doeff-vm-core
       │                               │
       └──── both linked into ─────────┘
                    │
              doeff-vm (cdylib)
```

**INV-1**: `doeff-vm-core` MUST NOT depend on `doeff-core-effects`. The dependency is strictly one-directional: effects depend on the VM core, never the reverse.

**INV-2**: `doeff-vm` (cdylib) depends on both and is the ONLY `cdylib` in the workspace. All `#[pyclass]` types from both rlibs are registered in the single module init.

### PyO3 Constraint

Per [PyO3 issue #1444](https://github.com/PyO3/pyo3/issues/1444): `#[pyclass]` stores the Python type object in a process-global `static`. If two separate `.so` files define the same `#[pyclass]`, Python sees them as different types. Therefore:

- `doeff-vm-core` is an `rlib` (statically linked), NOT a `cdylib`
- `doeff-core-effects` is an `rlib` (statically linked), NOT a `cdylib`
- Only `doeff-vm` is a `cdylib` — the single `.so` Python loads

## ADRs (Architecture Decision Records)

### ADR-1: GetExecutionContext Stays in VM Core

**Decision**: `PyGetExecutionContext`, `PyExecutionContext`, `make_get_execution_context_effect()`, and `is_execution_context_effect()` remain in `doeff-vm-core`.

**Rationale**: The error enrichment path (`vm.rs:878`) synthesizes a `GetExecutionContext` effect to collect handler chain context when an exception occurs. This is VM-internal semantics — not a user-space effect. The VM must be able to create and recognize this effect without depending on the effects crate.

**Consequence**: `vm.rs:657` (`is_execution_context_effect()`) is NOT a coupling violation. It checks a VM-internal type that lives in the same crate.

### ADR-2: Dead Code `is_local_effect` Removed

**Decision**: Remove the `is_local_effect()` function from `vm.rs:668-677`.

**Rationale**: `is_local_effect()` is defined but never called anywhere in the codebase. Dead code that references specific effect types in the VM core has no justification. If Local-effect identity checking is needed in the future, the handler (`ReaderHandlerFactory` / `LazyAskHandlerFactory`) can implement it via `can_handle()`.

### ADR-3: Single cdylib Architecture

**Decision**: All Rust code links into one `doeff_vm` cdylib. No separate Python extension packages for effects.

**Rationale**: PyO3 `#[pyclass]` type identity breaks across `.so` boundaries (issue #1444). The `rlib` → `cdylib` pattern gives compiler-enforced crate boundaries with zero runtime cost.

**Consequence**: Python continues to `import doeff_vm` as a single package. The internal crate split is invisible to Python users.

### ADR-4: Registration via Module Init

**Decision**: `doeff-core-effects` exposes a `register_all(module: &Bound<'_, PyModule>)` function that the cdylib's `#[pymodule]` init calls to register all effect classes and sentinel objects.

**Rationale**: Sentinel objects (`state`, `reader`, `writer`, `scheduler`, `lazy_ask`, `await_handler`, `result_safe`) and effect `#[pyclass]` types must be registered in the Python module. Moving them to `doeff-core-effects` means the registration code must also move. The cdylib init calls `doeff_core_effects::register_all(m)` as a single entry point.

### ADR-5: Handler Traits Stay in VM Core

**Decision**: `IRStreamFactory`, `IRStreamProgram`, `IRStream`, `Kleisli`, and the blanket `impl Kleisli for T: IRStreamFactory` remain in `doeff-vm-core`.

**Rationale**: These traits define the VM's handler protocol. Any crate that implements a handler must depend on these traits. They are the stable API surface of the VM.

### ADR-6: RustStore Stays in VM Core

**Decision**: `RustStore` remains in `doeff-vm-core`.

**Rationale**: `RustStore` is a pure generic data structure (3 fields: `state: HashMap<String, Value>`, `env: HashMap<HashedPyKey, Value>`, `log: Vec<Value>`). It has zero effect-specific imports. All handler trait methods (`start`, `resume`, `throw`) take `&mut RustStore` — it's a VM primitive, not an effect concern.

## Invariants

### Dependency Invariants

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| INV-1 | `doeff-vm-core` MUST NOT depend on `doeff-core-effects` | Cargo dependency graph (compile-time) + semgrep |
| INV-2 | Only `doeff-vm` (cdylib) may have `crate-type = ["cdylib"]` | Semgrep on Cargo.toml |
| INV-3 | `doeff-vm-core` MUST NOT import any type from `doeff-core-effects` | Compiler (Cargo) — impossible when dependency doesn't exist |

### Code-Level Invariants

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| INV-4 | `doeff-vm-core` must not contain `#[pyclass]` effect structs except `PyEffectBase`, `PyGetExecutionContext`, `PyExecutionContext` | Semgrep |
| INV-5 | `doeff-vm-core` must not contain `IRStreamFactory` implementations (only the trait definition + blanket impl) | Semgrep |
| INV-6 | `doeff-vm-core` must not contain `py.import("doeff.*")` calls | Semgrep |
| INV-7 | `doeff-vm` (cdylib) must not contain handler/effect logic — only glue code (PyVM wrapper, module init, `register_all` call) | Semgrep |

### Behavioral Invariants

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| INV-8 | Extracting effects/handlers MUST NOT change any observable behavior | Existing test suite (zero test modifications except import paths) |
| INV-9 | All existing `doeff_vm.*` Python imports MUST continue to work | Re-exports in cdylib module init |
| INV-10 | Build with `maturin develop --release` MUST produce a working `doeff_vm` package | CI + manual verification |

## Semgrep Rules

```yaml
rules:
  # =========================================================================
  # SPEC-VM-018: Core Effects Extraction Boundary Enforcement
  # =========================================================================

  # INV-4: No effect structs in VM core (except the 3 VM-internal ones)
  - id: vm-core-no-user-effect-structs
    pattern-regex: '#\[pyclass\(.*extends\s*=\s*PyEffectBase.*\)\]\s*pub struct (?!PyGetExecutionContext|PyExecutionContext)'
    message: |
      SPEC VIOLATION (INV-4): User-space effect struct found in doeff-vm-core.
      Effect structs (PyGet, PyPut, PyAsk, etc.) must live in doeff-core-effects.
      Only PyGetExecutionContext and PyExecutionContext are allowed in VM core
      (per ADR-1: VM-internal error enrichment effect).
    languages: [generic]
    severity: ERROR
    paths:
      include:
        - "**/doeff-vm-core/src/**"

  # INV-5: No IRStreamFactory implementations in VM core
  - id: vm-core-no-handler-factory-impls
    pattern-regex: 'impl IRStreamFactory for \w+Handler'
    message: |
      SPEC VIOLATION (INV-5): IRStreamFactory implementation found in doeff-vm-core.
      Handler factory implementations (StateHandlerFactory, ReaderHandlerFactory, etc.)
      must live in doeff-core-effects. VM core defines only the trait + blanket Kleisli impl.
    languages: [generic]
    severity: ERROR
    paths:
      include:
        - "**/doeff-vm-core/src/**"

  # INV-6: No Python doeff.* imports in VM core
  - id: vm-core-no-doeff-python-imports
    pattern-regex: 'py\.import\("doeff\.'
    message: |
      SPEC VIOLATION (INV-6): Python doeff.* import found in doeff-vm-core.
      The VM core must not import Python-side doeff packages. Effect/handler code
      that needs Python imports must live in doeff-core-effects.
    languages: [generic]
    severity: ERROR
    paths:
      include:
        - "**/doeff-vm-core/src/**"

  # INV-7: No handler/effect logic in cdylib glue
  - id: cdylib-no-handler-logic
    pattern-regex: 'impl IRStreamFactory for'
    message: |
      SPEC VIOLATION (INV-7): IRStreamFactory implementation found in doeff-vm cdylib.
      The cdylib should contain only glue code (PyVM wrapper, module init).
      Handler implementations belong in doeff-core-effects.
    languages: [generic]
    severity: ERROR
    paths:
      include:
        - "**/doeff-vm/src/**"
      exclude:
        - "**/doeff-vm-core/**"
        - "**/doeff-core-effects/**"
```

## Coupling Points & Resolution

### Resolved by Architecture

| Coupling | Location | Resolution |
|----------|----------|------------|
| `GetExecutionContext` isinstance in VM | `vm.rs:657` | Stays in VM core (ADR-1) — not a violation |
| `make_get_execution_context_effect()` | `vm.rs:878` | Stays in VM core (ADR-1) |
| `is_local_effect()` dead code | `vm.rs:668` | Remove (ADR-2) |
| `lib.rs` mixed re-exports | `lib.rs` | Split into separate crate `lib.rs` files |
| `PyStdlib` / `PySchedulerHandler` | `pyvm.rs` | Move to `doeff-core-effects/src/sentinels.rs` |
| Sentinel objects in module init | `pyvm.rs:4488-4578` | Move to `doeff-core-effects::register_all()` |
| 23 effect imports in `pyvm.rs` | `pyvm.rs:12-18` | Move with sentinels to effects crate |

### Requires Code Changes

| Coupling | Location | Fix |
|----------|----------|-----|
| `LazyAskHandler` imports `doeff.effects.semaphore` | `handler.rs ~L332` | Use Rust `PyCreateSemaphore::new()` directly (already `#[pyclass]`) |
| `task_cancelled_error()` imports `doeff.effects.spawn.TaskCancelledError` | `scheduler.rs:809` | Define `TaskCancelledError` as `#[pyclass(extends=PyRuntimeError)]` in doeff-core-effects |
| `parse_*_python_effect()` functions in `handler.rs` | Throughout handler.rs | Move alongside their handler factories to doeff-core-effects |

## Implementation Phases

### Phase 1: Create `doeff-vm-core` rlib

1. Create `packages/doeff-vm-core/` with `Cargo.toml` (`crate-type = ["rlib"]`)
2. Move all generic modules from `doeff-vm/src/` to `doeff-vm-core/src/`:
   - `vm.rs`, `kleisli.rs`, `ir_stream.rs`, `continuation.rs`, `segment.rs`, `frame.rs`
   - `dispatch.rs`, `dispatch_state.rs`, `ids.rs`, `value.rs`, `error.rs`, `do_ctrl.rs`
   - `py_shared.rs`, `py_key.rs`, `arena.rs`, `capture.rs`, `doeff_generator.rs`
   - `python_call.rs`, `driver.rs`, `step.rs`, `debug_state.rs`
   - `interceptor_state.rs`, `trace_state.rs`, `vm_logging.rs`, `rust_store.rs`
3. Move `PyEffectBase`, `PyGetExecutionContext`, `PyExecutionContext`, and dispatch helpers to `doeff-vm-core/src/effect.rs`
4. Move `IRStreamFactory`, `IRStreamProgram` TRAIT definitions (not impls) + blanket `Kleisli` impl to `doeff-vm-core/src/handler.rs`
5. `doeff-vm/Cargo.toml` adds dependency on `doeff-vm-core`
6. `doeff-vm/src/` re-exports everything from `doeff-vm-core` for backward compat

**Verification**: `make sync && uv run pytest && uv run pyright` — zero behavior change.

### Phase 2: Create `doeff-core-effects` rlib

1. Create `packages/doeff-core-effects/` with `Cargo.toml` (`crate-type = ["rlib"]`)
2. Move effect structs (22 `Py*` structs, excluding `PyGetExecutionContext`/`PyExecutionContext`) to `doeff-core-effects/src/effects/`
3. Move handler factory+program pairs (6 handlers) to `doeff-core-effects/src/handlers/`
4. Move scheduler (SchedulerHandler, SchedulerState, etc.) to `doeff-core-effects/src/scheduler/`
5. Move `PyStdlib`, `PySchedulerHandler`, sentinel objects to `doeff-core-effects/src/sentinels.rs`
6. Create `doeff-core-effects::register_all(m: &Bound<'_, PyModule>)` that registers all classes + sentinels
7. Fix `LazyAskHandler`: replace `py.import("doeff.effects.semaphore")` with direct Rust `PyCreateSemaphore::new()` etc.
8. Fix `task_cancelled_error()`: define `TaskCancelledError` as `#[pyclass]` in doeff-core-effects
9. `doeff-vm/Cargo.toml` adds dependency on `doeff-core-effects`
10. `doeff-vm` module init calls `doeff_core_effects::register_all(m)?`

**Verification**: `make sync && uv run pytest && uv run pyright` — zero behavior change.

### Phase 3: Clean Up + Enforce

1. Remove dead code `is_local_effect()` from `doeff-vm-core/src/vm.rs`
2. Remove re-exports of effect/handler types from `doeff-vm-core/src/lib.rs`
3. Add semgrep rules from this spec to `packages/doeff-vm-core/.semgrep.yaml`
4. Run `make lint` to verify boundary enforcement
5. Update `packages/doeff-vm/.semgrep.yaml` with INV-7 rule

**Verification**: `make sync && uv run pytest && uv run pyright && make lint` — all clean.

## Test Strategy

- **Zero test modifications** for behavior: all existing tests should pass with only import path changes (if any)
- **New boundary tests**: Add Cargo-level tests that verify `doeff-vm-core` compiles independently without `doeff-core-effects`
- **Semgrep CI**: Boundary rules run in `make lint` and block violations

## Risks

| Risk | Mitigation |
|------|-----------|
| Circular dependency between effect.rs dispatch helpers and handler impls | `dispatch_from_shared` and `dispatch_ref_as_python` are generic (operate on `PyShared`) — they stay in VM core |
| Test-only `Effect` enum in `effect.rs` references specific effect types | Move test-only enum to `doeff-core-effects` or keep as `#[cfg(test)]` in both crates |
| `Cargo.lock` churn from workspace restructure | Expected and acceptable |
| `maturin` build system needs update for workspace members | Update `pyproject.toml` to reference the workspace structure |
