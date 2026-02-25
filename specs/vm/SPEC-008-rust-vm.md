# SPEC-008: Rust VM for Algebraic Effects

## Status: Draft (Revision 16)

### Revision 16 Changelog

Changes from Rev 15. ASTStream as DoExpr; Expand unification; WithIntercept redesign.

| Tag | Section | Change |
|-----|---------|--------|
| **R16-A** | DoExpr hierarchy | **ASTStream is a DoExpr.** An ASTStream (streaming program) is promoted to a DoExpr variant. `Eval` can process both static DoExpr nodes (Pure, Map, FlatMap, etc.) and streaming ASTStream programs uniformly. This unifies the two evaluation paths. |
| **R16-B** | Expand unification | **`Expand(f, args)` = `Eval(Apply(f, args))`.** `Apply(f, args)` calls `f`, which returns a DoExpr (possibly an ASTStream). `Eval` evaluates the result. The separate `Expand` node is no longer semantically necessary — it becomes sugar for `Eval(Apply(...))`. The VM no longer needs to distinguish Apply-vs-Expand at the call site. |
| **R16-C** | Macro expansion model | **Program invocation is macro expansion.** `Apply(f, args)` = expand macro `f` with inputs `args`, producing IR (DoExpr). `Eval` = evaluate the produced IR. `@do` generators are lazy macros: they produce IR nodes incrementally (one DoCtrl per yield) via ASTStream. Static callables produce IR in one shot. Both go through the same `Eval(Apply(...))` path. |
| **R16-D** | WithIntercept redesign | **`WithIntercept(f, expr)` — interceptor is `Effect -> DoExpr`.** `f` is any callable that takes an effect and returns DoExpr. Interceptor invocation is `Eval(Apply(f, [effect]))`. No type filtering at VM level (moved to Python-side wrapper). No `is_do_callable` sniffing. `types`, `mode` removed from DoCtrl variant. See SPEC-WITH-INTERCEPT Rev 3. |
| **R16-E** | `interceptor_call_arg` deleted | **VM-INTERCEPT-003 resolved.** The `interceptor_call_arg` function that probed for `DoeffGeneratorFn` to decide Apply-vs-Expand calling convention is eliminated. All callables are invoked uniformly via `Apply`. |

### Revision 15 Changelog

Changes from Rev 14. Split `Delegate` into two operations per SPEC-VM-010 (non-terminal delegate / re-perform semantics). See SPEC-VM-010 for full design rationale.

| Tag | Section | Change |
|-----|---------|--------|
| **R15-A** | DoCtrl variants | **Added `Pass(effect?)` as terminal pass-through.** Old `Delegate` terminal semantics renamed to `Pass`. `Delegate` is now non-terminal (re-perform): handler receives result back via K_new continuation swap. |
| **R15-B** | Dispatch flow | **`Delegate` is non-terminal.** Handler yields `Delegate()`, VM captures K_new from handler state, outer handler receives K_new. Outer Resume sends value to delegating handler, which transforms and resumes original k_user. |
| **R15-C** | Terminal classification | **`Pass` is terminal, `Delegate` is not.** `is_terminal` updated accordingly. |

### Revision 14 Changelog

Changes from Rev 13. Introduces explicit `Perform` control node and separates effect values from control IR.

| Tag | Section | Change |
|-----|---------|--------|
| **R14-A** | Core hierarchy | **DoExpr is control IR only.** Effects are user-space data (`EffectValue`), not DoExpr nodes. The VM evaluates DoCtrl; effect resolution is represented explicitly with `Perform(effect)`. This supersedes Rev 13 binary statement `DoExpr = DoCtrl \| Effect`. |
| **R14-B** | DoCtrl variants | **Added `Perform(effect)` as a first-class DoCtrl variant.** `Perform` is the only control instruction that requests handler dispatch. [R15-A] `Delegate` is now non-terminal re-perform; `Pass(effect?)` is the terminal pass-through. |
| **R14-C** | Lowering boundary | **Source-level `yield effect` lowers to `yield Perform(effect)`.** Python UX stays unchanged while IR remains explicit. `run(effect_value)` is normalized to `run(Perform(effect_value))` at API boundary. |
| **R14-D** | Invocation model | **`Call` remains canonical application node; no `Apply` node is introduced.** KPC lowering and handler invocation semantics target `Call(...)` directly. |
| **R14-E** | Dispatch contract | **Handler invocation returns DoExpr control.** If host handler returns an effect value, runtime normalization wraps it as `Perform(effect)` before continuation. |
| **R15-A** | KPC model | **KPC is a call-time macro, not a runtime effect (doeff-13).** KPC handler removed. `KleisliProgramCall` no longer extends `PyEffectBase`. `KleisliProgram.__call__()` returns `Call` DoCtrl directly. See SPEC-KPC-001. |

### Revision 13 Changelog

Changes from Rev 12. Binary type hierarchy (DoExpr = DoCtrl | Effect), DoThunk eliminated, Pure/Map/FlatMap added.

| Tag | Section | Change |
|-----|---------|--------|
| **R13-A** | Type hierarchy | **Binary DoExpr hierarchy: DoExpr = DoCtrl \| Effect.** DoThunk eliminated. Generators are lazy ASTs — each yielded DoExpr is an expression node. Two categories: DoCtrl (fixed VM syntax) and Effect (open handler-dispatched data). No third category. |
| **R13-B** | DoCtrl variants | **Added Pure, Map, FlatMap.** `Pure(value)` is the literal value node (evaluates to value immediately, zero cost). `Map(source: DoExpr, f)` is functor map (replaces DerivedProgram). `FlatMap(source: DoExpr, binder)` is monadic bind. These are DoCtrl nodes — VM syntax, not effects. |
| **R13-C** | Call args | **`Call(f: DoExpr, args: [DoExpr], kwargs, meta)` takes DoExpr args.** VM evaluates args sequentially left-to-right. KPC handler can pre-resolve in parallel and emit Call with Pure(resolved) args. `f` is also a DoExpr — typically Pure(callable) or an effect that resolves to a callable. |
| **R13-D** | classify_yielded | **Binary classification: DoCtrlBase \| EffectBase.** DoThunkBase deleted. `classify_yielded` checks `isinstance(obj, DoCtrlBase)` → downcast to specific DoCtrl variant, or `isinstance(obj, EffectBase)` → `Yielded::Effect(obj)`. No third path. |
| **R13-E** | PythonCall::StartProgram | **Renamed to `PythonCall::EvalDoExpr`.** Semantics: driver evaluates a DoExpr node. For Pure(callable), calls `callable()` and expects a generator. For Call nodes, evaluates args first, then calls. For effects, this path is not used (effects go through dispatch). The key insight: DoCtrl is the VM's instruction set, not a Python-level concept. |
| **R13-F** | Eval framing | **`DoCtrl::Eval(expr: DoExpr, handlers)` evaluates DoExpr nodes.** `expr` can be Pure(callable), Call(...), Map(...), FlatMap(...), or an Effect. VM creates a continuation with the handler chain and evaluates the DoExpr within it. This is the VM's expression evaluator — not a Python call. |
| **R13-G** | Call arg evaluation | **Full spec for `DoCtrl::Call` with DoExpr args.** Added `PendingPython::CallEvalProgress` with `CallEvalPhase` (EvalF/EvalArg/Invoke) to track multi-step evaluation. Fast path: all Pure args → extract and call directly (ONE NeedsPython for the invocation). Slow path: evaluate f, then args[0..n] sequentially via `eval_do_expr()`. `eval_do_expr()` short-circuits Pure(value) without Python round-trip via GIL-free tag read. `try_call_fast_path()` checks all-Pure condition GIL-free. Also: `FlatMapBinderResult` pending state for two-step binder evaluation. |
| **R13-H** | Stale references | **Fixed remaining stale StartProgram/DoThunk/to_generator references.** ASCII diagram updated to binary DoCtrl/Effect/Unknown. HandleYield table (INV-14) updated. INV-15 classification updated. `run()` entry point uses `classify_program_input()` instead of `to_generator()`. Legacy Specs section updated to reflect binary hierarchy. `async_run` Python example updated. |
| **R13-I** | GIL-free tag dispatch | **`DoExprTag` discriminant for GIL-free type checking.** `PyDoCtrlBase` and `PyEffectBase` carry an immutable `tag: u8` field (`#[pyclass(frozen)]`). `DoExprTag` is a `#[repr(u8)]` enum: Pure=0, Call=1, ..., Effect=128, Unknown=255. `classify_yielded` reads the tag without GIL via unsafe pointer access to frozen struct data. `eval_do_expr` and `try_call_fast_path` also use tag-based dispatch — Pure values are extracted GIL-free. `extract_do_ctrl` reads variant-specific fields (`.value`, `.f`, `.args`, etc.) GIL-free from frozen pyclasses. Only actual Python function invocation requires NeedsPython/GIL. |

### Revision 12 Changelog

Changes from Rev 11. Clarifies Call semantics and eliminates `to_generator()` from the Rust VM.

| Tag | Section | Change |
|-----|---------|--------|
| **R12-A** | DoCtrl::Call | **SUPERSEDED BY R13-C.** `DoCtrl::Call(f: DoExpr, args: [DoExpr], kwargs, meta)` takes DoExpr arguments. VM evaluates args sequentially left-to-right. `f` is a DoExpr (typically Pure(callable)). No distinction between "DoThunk path" and "kernel path" — both are Call with DoExpr args. |
| **R12-B** | `to_generator()` boundary | **SUPERSEDED BY R13-A.** DoThunk eliminated. Generators are lazy ASTs. The VM evaluates DoExpr nodes (Pure, Call, Map, FlatMap, Effect). No `to_generator()` at VM level. |
| **R12-C** | KPC kernel type | **SUPERSEDED BY R13-C.** KPC handler emits `Call(f: Pure(kernel), args: [Pure(arg1), Pure(arg2), ...], kwargs, meta)`. VM evaluates each arg DoExpr, then calls `kernel(*resolved_args, **resolved_kwargs)`. |
| **R12-D** | DoExpr Input Rule | **SUPERSEDED BY R13-A.** DoExpr = DoCtrl \| Effect. No DoThunk. Generators yield DoExpr nodes. VM evaluates them. |

### Revision 11 Changelog

Changes from Rev 10. Effects are data — the VM is a dumb pipe.

| Tag | Section | Change |
|-----|---------|--------|
| **R11-A** | Effect types | **All Rust-handled effects are `#[pyclass]` structs.** `Get`, `Put`, `Ask`, `Tell`, `Modify` defined in Rust, exposed to Python. Scheduler effect pyclasses defined in SPEC-SCHED-001. **[SUPERSEDED BY R15-A / SPEC-KPC-001]** ~~`KleisliProgramCall` (KPC) is also a `#[pyclass(frozen, extends=PyEffectBase)]` struct — it carries `kleisli_source`, `args`, `kwargs`, `function_name`, `execution_kernel`, `created_at`. Auto-unwrap strategy is NOT stored on KPC — it is the handler's responsibility to compute from `kleisli_source` annotations at dispatch time (see SPEC-TYPES-001 §3).~~ [R15-A: KPC is no longer an effect or `#[pyclass(extends=PyEffectBase)]`. `KleisliProgram.__call__()` returns `Call` DoCtrl directly. See SPEC-KPC-001.] Python imports these types from the Rust crate. |
| **R11-B** | Effect enum | **`Effect` typed enum REMOVED.** No `Effect::Get { key }`, `Effect::Put { .. }`, etc. Effects flow through dispatch as opaque `Py<PyAny>`. The VM does not know effect internals. Handlers downcast to concrete `#[pyclass]` types themselves. |
| **R11-C** | classify_yielded | **Effect classification is a single isinstance check.** `classify_yielded` checks `isinstance(obj, EffectBase)` → `Yielded::Effect(obj)`. No field extraction. No per-effect-type arms. No string matching. The classifier does not touch effect data. |
| **R11-D** | Handler traits | **Handler receives opaque effect.** `RustHandlerProgram::start()` takes `Py<PyAny>` (not `Effect` enum). `RustProgramHandler::can_handle()` takes `&Bound<'_, PyAny>`. Handlers downcast via `obj.downcast::<Get>()` etc. using `Python::with_gil()`. |
| **R11-E** | start_dispatch | **Dispatch is opaque.** `start_dispatch(effect: Py<PyAny>)`. `DispatchContext.effect` is `Py<PyAny>`. `Delegate` carries `Py<PyAny>`. |
| **R11-F** | Dispatch bases | **All type-dispatch bases are Rust `#[pyclass(subclass)]`.** `EffectBase`, `DoCtrlBase` defined in Rust. [R13-D: DoThunkBase deleted — binary hierarchy only.] [R13-I: GIL-free tag-based dispatch replaces `is_instance_of`. Each base carries an immutable `tag: u8` discriminant. VM reads the tag without GIL for classification and variant dispatch.] Concrete types extend their base via `#[pyclass(extends=...)]`. Python user types subclass normally. |

**CODE-ATTENTION items** (implementation work needed — R11):
- `effect.rs`: Delete `Effect` enum entirely. Add `#[pyclass(frozen)]` structs: `PyGet`, `PyPut`, `PyAsk`, `PyTell`, `PyModify`. Scheduler effect pyclasses are defined in SPEC-SCHED-001 (implemented in `scheduler.rs`). [SUPERSEDED BY R15-A / SPEC-KPC-001] ~~Add `PyKPC` (`#[pyclass(frozen, extends=PyEffectBase)]`) with fields: `kleisli_source: Py<PyAny>`, `args: Py<PyTuple>`, `kwargs: Py<PyDict>`, `function_name: String`, `execution_kernel: Py<PyAny>`, `created_at: Py<PyAny>`. KPC does NOT carry `auto_unwrap_strategy` — the handler computes it from `kleisli_source` annotations.~~
- `effect.rs` (or new `bases.rs`): Add `#[pyclass(subclass, frozen)]` base classes: `PyEffectBase { tag: u8 }`, `PyDoCtrlBase { tag: u8 }`. [R13-D: DoThunkBase deleted — binary hierarchy.] [R13-I: tag field enables GIL-free classification.] Add `DoExprTag` enum (`#[repr(u8)]`). All concrete types extend their base via `#[pyclass(extends=...)]` and set tag at construction. [R11-F]
- `handler.rs`: Change `RustProgramHandler::can_handle(&self, effect: &Effect)` → `can_handle(&self, py: Python<'_>, effect: &Bound<'_, PyAny>)`. Change `RustHandlerProgram::start(&mut self, effect: Effect, ...)` → `start(&mut self, py: Python<'_>, effect: Py<PyAny>, ...)`.
- `vm.rs`: Change `start_dispatch(effect: Effect)` → `start_dispatch(py: Python<'_>, effect: Py<PyAny>)`. Change `DispatchContext.effect: Effect` → `effect: Py<PyAny>` (or `PyShared<PyAny>`). Change `find_matching_handler` to pass `py` + `&Bound`. Delete `Yielded::Effect(Effect)` → `Yielded::Effect(Py<PyAny>)`.
- `pyvm.rs`: Delete entire string-based `match type_str { ... }` block (~148 lines). Delete all effect field extraction. Delete `is_effect_object()` Python import path. [R13-I: Replace classify_yielded with GIL-free tag read via `read_do_expr_tag()`. No `is_instance_of` needed — tag discriminant replaces MRO-based type checks.] [R13-D: Binary classification only.]
- `pyvm.rs`: Delete individual DoCtrl isinstance checks (PyWithHandler, PyResume, etc.) — dispatch on `DoExprTag` value instead. [R13-I]
- `pyvm.rs`: Update all DoCtrl pyclasses (PyWithHandler, PyResume, PyTransfer, PyDelegate) to use `extends=PyDoCtrlBase`.
- `step.rs`: Change `Yielded::Effect(Effect)` → `Yielded::Effect(Py<PyAny>)`. Delete `Yielded::Program` variant.
- State/Reader/Writer handler impls: Rewrite `start()` to downcast `Py<PyAny>` → `PyRef<PyGet>` etc.
- Scheduler handler impl: See SPEC-SCHED-001 for effect pyclasses and handler implementation.
- [SUPERSEDED BY R15-A / SPEC-KPC-001] ~~KPC handler impl: Rewrite `start()` to downcast `Py<PyAny>` → `PyRef<PyKPC>`. Handler reads `kleisli_source` to compute auto-unwrap strategy from annotations at dispatch time. Strategy computation is handler-internal — different KPC handlers may use different strategies.~~
- Python side: `doeff/types.py` or wherever `EffectBase` is defined — delete Python class, import from `doeff_vm` instead. Same for any Python-side DoCtrl bases. [R13-D: DoThunkBase deleted — no third base.] [SUPERSEDED BY R15-A / SPEC-KPC-001] ~~Delete Python `KleisliProgramCall` class — replace with `PyKPC` imported from `doeff_vm`. Delete `_AutoUnwrapStrategy` from `KleisliProgramCall` — it moves into the KPC handler.~~

**CODE-ATTENTION items** (carried from R10):
- `frame.rs`: Add `CallMetadata::anonymous()` constructor
- `rust_vm.py`: Delete `_LegacyRunResult` class, delete old PyVM fallback path (lines 64-84)
- `effects/spawn.py`: Delete `Promise.complete()`, `Promise.fail()`, `Task.join()` deprecated methods
- `effects/gather.py`: Delete backwards compat alias
- `effects/future.py`: Delete backwards compat alias
- `effects/scheduler_internal.py`: Delete backwards compat aliases (lines 196+, 221+)
- `core.py`: Delete compat re-exports or entire module
- `_types_internal.py:35`: Delete vendored type re-export comment/code

### Revision 9–10 Changelog (historical)

R9: Added `Call`, `GetCallStack`, `Eval` DoCtrl variants. `CallMetadata` on frames.
R10: Removed `Yielded::Program`, string-based classify, backward-compat paths.
Both superseded by R11 — opaque effect architecture replaces typed `Effect` enum.

### Revision 8 Changelog

Changes from Rev 7, grouped by section. Each change is marked with a tag
so reviewers can accept/reject individually.

| Tag | Section | Change |
|-----|---------|--------|
| **R8-A** | ADR-2 | Rewritten: "Unified Handler Protocol" replaces "No Built-in Bypass". Drops stdlib as separate concept. |
| **R8-B** | ADR-8 (new) | New ADR: Drop `Handler::Stdlib`, unify to `RustProgram`/`Python` only. |
| **R8-C** | ADR-9 (new) | New ADR: PyO3-exposed primitive types (`WithHandler`, `Resume`, `Delegate`, `Transfer`, `K`). |
| **R8-D** | ADR-10 (new) | New ADR: Handler identity preservation — `GetHandlers` returns original Python objects. |
| **R8-E** | ADR-11 (new) | New ADR: Store/env initialization via `put_state`/`put_env` + `env_items` extraction. |
| **R8-F** | Principle 3 | Removed "immediate stdlib handler" signature. Only `RustHandlerProgram` + Python handler. |
| **R8-G** | Handler enum | Reduced to two variants: `RustProgram` and `Python`. `Stdlib` variant deleted. |
| **R8-H** | Stdlib Handlers | `StdlibHandler`, `HandlerAction`, `HandlerContext(ModifyPending)`, `NeedsPython` deleted. Replaced by `RustProgramHandler` impls. |
| **R8-I** | Handler section | `StateHandlerFactory`, `ReaderHandlerFactory`, `WriterHandlerFactory` added as `RustProgramHandler` implementations. |
| **R8-J** | Public API Contract (new) | New section: `run()`/`async_run()` contract, `RunResult`, `@do`/`Program[T]`, store init/extract flow, handler nesting order. Closes SPEC-009 support gaps. |

---

## Summary

This spec defines a **Rust-based VM** for doeff's algebraic effects system, with Python integration via PyO3.

**Key insight**: The VM core (segments, frames, dispatch, primitives) is unified Rust. Python generators are leaf nodes at the FFI boundary.

```
┌─────────────────────────────────────────────────────────────────┐
│  Python Layer (doeff library)                                   │
│    - @do decorated generators (user code)                       │
│    - Python handlers (user-defined effects)                     │
│    - High-level API (run, with_handler, etc.)                   │
└─────────────────────────────────────────────────────────────────┘
                              │ PyO3 FFI
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Rust VM (doeff-vm crate)                                       │
│                                                                 │
│    Segments          Frames           Dispatch                  │
│    ┌────────┐       ┌────────┐       ┌────────────┐            │
│    │ marker │       │ PyGen  │       │ dispatch_  │            │
│    │ frames │◄─────►│ RustProg│      │ stack      │            │
│    │ caller │       │ RustCb │       │            │            │
│    │ scope  │       └────────┘       │ visible_   │            │
│    └────────┘                        │ handlers() │            │
│        │                             └────────────┘            │
│        │              Primitives                                │
│        │             ┌─────────────────────────────┐           │
│        └────────────►│ Resume, Transfer, Delegate  │           │
│                      │ WithHandler, Call,           │           │
│                      │ GetContinuation, GetCallStack│          │
│                      └─────────────────────────────┘           │
│                                                                 │
│    3-Layer State Model                                          │
│    ┌──────────────────────────────────────────────┐            │
│    │ L1: Internals (hidden)                       │            │
│    │     dispatch_stack, segments, callbacks      │            │
│    ├──────────────────────────────────────────────┤            │
│    │ L2: RustStore (standard handler state)        │            │
│    │     state, env, log (HashMap/Vec<Value>)     │            │
│    ├──────────────────────────────────────────────┤            │
│    │ L3: PyStore (optional escape hatch)          │            │
│    │     Python dict for user handlers            │            │
│    └──────────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Design Decisions

### ADR-1: Hybrid Frame Architecture

**Decision**: Rust manages the frame stack; Python generators are leaf nodes.

**Rationale**:
- Rust controls continuation structure (segments, caller links, scope_chain)
- Python generators handle user code execution
- Frame switching is Rust-native (fast)
- Python calls happen at frame boundaries (GIL acquired/released cleanly)

### ADR-2: Unified Handler Protocol (No Stdlib Special Case)

**Decision**: ALL handlers — Rust-native and Python-implemented — share one
dispatch protocol. There is no separate "stdlib" handler path. The `Handler`
enum has exactly two variants: `RustProgram` and `Python`.

**Rationale**:
- Algebraic effects principle: "handlers give meaning to effects"
- Users can intercept, override, or replace any effect (logging, persistence, testing)
- Single dispatch path simplifies spec and implementation
- Rust-native handlers (state, reader, writer, and the scheduler per SPEC-SCHED-001)
  are an **optimization**, not a protocol difference — they implement the same `RustProgramHandler` trait
- No hard-coded effect→handler matching; each handler's `can_handle()` decides

**Performance**: Rust-native handlers avoid Python calls and GIL acquisition.
The `RustProgramHandler` trait adds negligible overhead vs. the old `HandlerAction`
path (one vtable call + one match arm).

**Handler Installation** (all handlers are explicit, no defaults):
```python
from doeff import run, WithHandler
from doeff.handlers import state, reader, writer

# Standard handlers are just handlers — no special treatment
result = run(
    my_program(),
    handlers=[state, reader, writer],
    store={"x": 0},
)

# User can replace any standard handler with custom implementation
result = run(
    my_program(),
    handlers=[my_persistent_state, reader, writer],
)

# Handlers composable via WithHandler anywhere
@do
def my_program():
    result = yield WithHandler(
        handler=cache_handler,
        expr=sub_program(),
    )
    return result
```

**Built-in Scheduler (Explicit, see SPEC-SCHED-001)**:
```python
from doeff.handlers import scheduler
result = run(my_program(), handlers=[scheduler, state, reader, writer])
```

### ADR-3: GIL Release Strategy

**Decision**: Release GIL during pure Rust computation, reacquire at Python boundaries.

**Rationale**:
- Rust frame management doesn't need GIL
- Pure Rust handlers (State, Reader) don't need GIL
- Python handler invocation requires GIL
- Enables better concurrency when multiple threads run independent programs

### ADR-4: Synchronous Rust VM

**Decision**: Rust VM is synchronous. Async is handled by Python wrapper.

**Invariant: Execution-model agnostic VM core.**
The Rust VM step loop is synchronous and must not depend on, import, or integrate with any
external execution runtime (asyncio, threading, Ray, etc.). External execution models are
bridged only through effects (for example, `Await` and `ExternalPromise`) and handled at the
Python runtime layer.

**Rationale**:
- Simpler FFI boundary (no async trait objects across FFI)
- Python's asyncio can call `vm.step()` in a loop
- Rust `async` would complicate lifetime management with PyO3
- Can add async Rust later if needed

### ADR-4a: Asyncio Integration (Reference)

**Decision**: Provide a Python-level async driver (`async_run` / `VM.run_async`) and the
`PythonAsyncSyntaxEscape` DoCtrl for handlers that need `await`.

**Rationale**:
- Python's asyncio APIs require an `async def` context and a running event loop
- Handlers execute during `step()` (synchronous) and cannot call asyncio directly
- `PythonAsyncSyntaxEscape` lets handlers request "run this action in async context"
- `sync_run`/`VM.run` remains the canonical path; `async_run` is a wrapper for interop

**Invariant**: `sync_run` MUST NOT see `PythonAsyncSyntaxEscape` / `CallAsync`. It raises
`TypeError` if it does.

### ADR-5: Typed Store with Escape Hatch

**Decision**: Known VM state in typed Rust fields; user state in `HashMap<String, Value>`.

**Rationale**:
- `handlers`, `dispatch_stack`, `consumed_ids` are VM internals → typed Rust
- User state (Get/Put) can be arbitrary Python objects → Value::Python
- Type safety for VM operations; flexibility for user code
- Can optimize hot paths (state lookups) in Rust

### ADR-6: Callbacks in VM Table (FnOnce Support)

**Decision**: FnOnce callbacks are stored in a VM-owned table; Frames hold CallbackId.

**Rationale**:
- `Box<dyn FnOnce>` is not Clone, but Frames need to be cloneable for continuation capture
- CallbackId is Copy, so Frames become Clone
- On execution, `callbacks.remove(id)` consumes the FnOnce
- Clean separation of "what to run" (callback) from "continuation structure" (frame)

### ADR-7: Mutable Execution Segments + Snapshot Continuations

**Decision**: Running Segments have mutable `Vec<Frame>`; captured Continuations hold `Arc<Vec<Frame>>` snapshots.

**Rationale**:
- Segments need push/pop during execution
- Continuations need immutable snapshots for one-shot semantics
- Resume materializes snapshot back to mutable Vec (shallow clone, Frame is small)
- Future optimization: persistent cons-list for O(1) sharing

### ADR-8: Drop Handler::Stdlib — Unified to RustProgram/Python [R8-B]

**Decision**: The `Handler` enum has exactly two variants: `RustProgram` and
`Python`. The former `Handler::Stdlib` variant is removed. State, Reader, and
Writer handlers become `RustProgramHandler` implementations — the same trait
the scheduler (SPEC-SCHED-001) already uses.

**Rationale**:
- `Handler::Stdlib` had a separate dispatch path: hard-coded `can_handle()` matching,
  direct `RustStore` mutation via `HandlerAction`, and a special `NeedsPython` flow
  for `Modify`. This created three dispatch protocols instead of one.
- `Handler::RustProgram` already provides a generator-like protocol
  (`start`/`resume`/`throw`) that handles the same cases — including calling Python
  mid-handler (the `Modify` modifier callback).
- Unifying to two variants means one dispatch path, one matching mechanism
  (`can_handle()`), and one handler-invocation protocol per variant.
- The scheduler (SPEC-SCHED-001) is already a `RustProgram` handler. State, Reader,
  and Writer are simpler — they're a subset of what the scheduler does.

**What is deleted**: `StdlibHandler` enum, `HandlerAction` enum,
`HandlerContext(ModifyPending)`, `NeedsPython` variant, `continue_after_python()`.

**What replaces it**: `StateHandlerFactory`, `ReaderHandlerFactory`,
`WriterHandlerFactory` — each implementing `RustProgramHandler`.

### ADR-9: PyO3-Exposed Primitive Types [R8-C]

**Decision**: Control primitives and composition primitives are Rust `#[pyclass]`
types exposed to Python, not Python dataclasses parsed by `classify_yielded`.

**Types exposed**:
- `WithHandler(handler, expr)` — composition primitive (usable anywhere)
- `Resume(k, value)` — dispatch primitive (handler-only)
- `Delegate(effect?)` — dispatch primitive, non-terminal re-perform (handler-only) [R15-A]
- `Pass(effect?)` — dispatch primitive, terminal pass-through (handler-only) [R15-A]
- `Transfer(k, value)` — dispatch primitive (handler-only)
- `K` — opaque continuation handle (no Python-visible fields)

**Rationale**:
- Eliminates fragile attribute-name parsing in `classify_yielded` (e.g., reading
  `.body` vs `.program` from a Python dataclass)
- Type checking via `isinstance` against a Rust-defined class is faster and
  unambiguous
- The `K` type is created by the VM and passed to Python handlers; Python code
  can pass it around but cannot inspect or construct it
- `WithHandler` field names are defined once in Rust, no Python/Rust mismatch

### ADR-10: Handler Identity Preservation [R8-D]

**Decision**: `GetHandlers` returns the original Python objects the user passed
to `run()` or `WithHandler`, at `id()` level.

**Mechanism**:
- When a handler is installed (via `WithHandler` or `run()`), the VM stores the
  original Python object (`PyShared`) alongside the internal `Handler` variant
- For `Handler::RustProgram` handlers recognized from Python sentinel objects
  (e.g., `state`, `reader`, `writer`), the VM stashes the sentinel's `PyShared`
- For `Handler::Python` handlers, the callable is already stored as `PyShared`
- `GetHandlers` traverses the scope chain and returns the stashed Python objects

**Rationale**:
- Users expect `state in (yield GetHandlers())` to work
- Handler identity matters for patterns like "am I inside this handler?"
- The `HandlerEntry` struct gains a `py_identity: Option<PyShared>` field

### ADR-11: Store/Env Initialization and Extraction [R8-E]

**Decision**: PyVM exposes `put_state()`, `put_env()`, `env_items()` for the
`run()` function to seed initial state and read back results.

**New PyVM methods**:
- `put_state(key: str, value: PyAny)` — sets `RustStore.state[key]`
- `put_env(key: str, value: PyAny)` — sets `RustStore.env[key]`
- `env_items() -> dict` — returns `RustStore.env` as Python dict

**Existing** (unchanged):
- `state_items() -> dict` — returns `RustStore.state` as Python dict
- `logs() -> list` — returns `RustStore.log` as Python list

**Rationale**:
- The `run()` function (SPEC-009) takes `env={}` and `store={}` parameters
- It needs to seed the VM before running and extract results after
- These methods are implementation details — users never call them directly

### ADR-15: Explicit Perform Boundary (EffectValue vs DoExpr) [R14-A, R14-B]

**Decision**: Separate effect data from control evaluation.

- `EffectValue` is user-space data (open world; stdlib and custom effects).
- `DoExpr` is control IR evaluated by the VM.
- Effect dispatch is represented explicitly as `Perform(effect: EffectValue)`.

**Rationale**:
- Removes ambiguity between "an effect object exists" and "an effect is being resolved now".
- Clarifies handler semantics: handlers consume effect data and return control expressions.
- Preserves user ergonomics (`yield Ask("x")`) while keeping IR explicit via lowering.

**Normative consequences**:
- Source-level `yield effect` lowers to `yield Perform(effect)`.
- `run(effect_value)` normalizes to `run(Perform(effect_value))`.
- `Call` remains the single invocation primitive; no parallel `Apply` node.
- `Delegate(effect?)` carries effect data only; outer scope performs dispatch.

---

## Core Design Principles

### Principle 1: Segment = Delimited Continuation Frame (Rust)

Segment is a Rust struct representing a delimited continuation frame:
- Frames (K) - Vec of Rust Frame enums (mutable during execution)
- Caller link (Option<SegmentId>)
- Marker (handler identity this segment belongs to)
- scope_chain (Vec<Marker>) - evidence vector snapshot
- kind (Normal or PromptBoundary)

### Principle 2: Three Distinct Contexts

| Context | What it is | Tracked by |
|---------|------------|------------|
| User code location | Where effect was performed | `k_user.segment_id` |
| Handler scope boundary | Where WithHandler was installed | **PromptSegment** (kind=PromptBoundary) |
| Handler execution | Where handler code runs | `handler_exec_seg` |

### Principle 3: Explicit Continuations [R8-F]

Handlers (Rust or Python) receive continuations explicitly. There is one
handler protocol with two implementations:

```rust
// Rust handler (generator-like, used by state/reader/writer/scheduler per SPEC-SCHED-001)
trait RustHandlerProgram {
    fn start(&mut self, effect: Effect, k: Continuation, store: &mut RustStore) -> RustProgramStep;
    fn resume(&mut self, value: Value, store: &mut RustStore) -> RustProgramStep;
    fn throw(&mut self, exc: PyException, store: &mut RustStore) -> RustProgramStep;
}

// Python handler (via PyO3)
// def handler(effect, k) -> Program[Any]
// VM evaluates the handler's return (a DoExpr) as a generator. [R13-E]
```

Both produce the same observable behavior: `Resume(k, value)`, `Delegate(effect)`,
or `Transfer(k, value)`. The distinction is purely an implementation optimization.

### Principle 4: Ownership and Lifetimes

All Rust data structures use ownership semantics:
- Segments owned by VM's segment arena
- Continuations hold SegmentId + Arc<frames snapshot>
- Callbacks owned by VM's callback table
- PyObjects use `PyShared` (`Arc<Py<PyAny>>`) for GIL-free clonable storage
- No `unsafe` in core logic (PyO3 handles FFI safety)

---

## Rust Data Structures

### Marker and IDs

```rust
/// Unique identifier for prompts/handlers.
/// All segments under the same with_handler share the same Marker.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct Marker(u64);

/// Unique identifier for segments (arena index)
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct SegmentId(u32);

// SegmentId(0) may be used as a placeholder for unstarted continuations.

/// Unique identifier for continuations (one-shot tracking)
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct ContId(u64);

/// Unique identifier for dispatches
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct DispatchId(u64);

/// Unique identifier for callbacks in VM table
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct CallbackId(u32);

/// [Q11] Unique identifier for spawned tasks (see SPEC-SCHED-001)
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct TaskId(pub u64);

/// [Q11] Unique identifier for promises (see SPEC-SCHED-001)
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct PromiseId(pub u64);

impl Marker {
    pub fn fresh() -> Self {
        static COUNTER: AtomicU64 = AtomicU64::new(1);
        Marker(COUNTER.fetch_add(1, Ordering::Relaxed))
    }
}

// Marker(0) is reserved for internal placeholders (e.g., unstarted continuations).
```

### PyShared (GIL-free clonable reference)

```rust
/// GIL-free clonable Python object reference (`Arc<Py<PyAny>>`).
/// `.clone()` is atomic increment — no GIL assertion on free-threaded 3.14t.
///
/// Used throughout the VM wherever Python objects need to be stored in
/// Clone-able Rust data structures (frames, continuations, dispatch contexts).
/// Raw `Py<PyAny>` clone calls `Py::clone_ref()` which asserts GIL on 3.14t;
/// wrapping in Arc avoids this.
#[derive(Debug, Clone)]
pub struct PyShared(Arc<Py<PyAny>>);
```

### Frame (Clone-able via CallbackId)

```rust
/// A frame in the continuation stack.
/// 
/// Frames are Clone because they may be captured in continuations.
/// FnOnce callbacks are stored separately in VM.callbacks table.
#[derive(Debug, Clone)]
pub enum Frame {
    /// Rust-native return frame (for standard handlers).
    /// The actual callback is in VM.callbacks[cb].
    RustReturn {
        cb: CallbackId,
    },
    
    /// Rust program handler frame (generator-like, no Python calls).
    /// program is a shared reference (see RustProgramRef in Handler section).
    RustProgram {
        program: RustProgramRef,
    },
    
    /// Python generator frame (user code or Python handlers)
    PythonGenerator {
        /// The Python generator object (GIL-free clonable reference)
        generator: PyShared,
        /// Whether this generator has been started (first __next__ called)
        started: bool,
        /// Call stack metadata — always populated via DoCtrl::Call. [R9-C, R10-A]
        /// Option<> is for WithHandler body frames (no call context), NOT for
        /// a legacy Yielded::Program path (which is removed).
        metadata: Option<CallMetadata>,
    },
}

/// Metadata about a program call for call stack reconstruction. [R9-D]
///
/// Extracted by the driver (with GIL) during classify_yielded or by
/// RustHandlerPrograms that emit Call primitives. Stored on PythonGenerator
/// frames. Read by GetCallStack (no GIL needed for the Rust fields).
#[derive(Debug, Clone)]
pub struct CallMetadata {
    /// Human-readable function name (e.g., "fetch_user", from KPC.function_name)
    pub function_name: String,
    /// Source file where the @do function is defined
    pub source_file: String,
    /// Line number in source file
    pub source_line: u32,
    /// Optional: reference to the full KleisliProgramCall Python object.
    /// Enables rich introspection (args, kwargs, kleisli_source) via GIL.
    /// None for non-KPC programs or when metadata is extracted from Rust-side only.
    /// [R15-A] Under the macro model, CallMetadata is populated at
    /// `KleisliProgram.__call__()` time (not by a KPC handler). See SPEC-KPC-001.
    pub program_call: Option<PyShared>,
}

/// Callback type stored in VM.callbacks table.
/// Consumed (removed) when executed.
/// +Sync required for free-threaded Python 3.14t compatibility.
pub type Callback = Box<dyn FnOnce(Value, &mut VM) -> Mode + Send + Sync>;
```

### Segment

```rust
/// Segment kind - distinguishes prompt boundaries from normal segments.
#[derive(Debug, Clone)]
pub enum SegmentKind {
    /// Normal segment (user code, handler execution)
    Normal,
    /// Prompt boundary segment (created by WithHandler)
    PromptBoundary {
        /// Which handler this prompt delimits
        handled_marker: Marker,
    },
}

/// Delimited continuation frame.
/// 
/// Represents a continuation delimited by a prompt (marker).
/// Frames are mutable during execution; captured via Arc snapshot.
#[derive(Debug)]
pub struct Segment {
    /// Handler identity this segment belongs to
    pub marker: Marker,
    
    /// Frames in this segment (stack, top = LAST index for O(1) pop)
    pub frames: Vec<Frame>,
    
    /// Caller link - who to return value to
    pub caller: Option<SegmentId>,
    
    /// Evidence vector - handlers in scope [innermost, ..., outermost]
    pub scope_chain: Vec<Marker>,
    
    /// Segment kind (Normal or PromptBoundary)
    pub kind: SegmentKind,
}

impl Segment {
    pub fn new(marker: Marker, caller: Option<SegmentId>, scope_chain: Vec<Marker>) -> Self {
        Segment {
            marker,
            frames: Vec::new(),
            caller,
            scope_chain,
            kind: SegmentKind::Normal,
        }
    }
    
    pub fn new_prompt(
        marker: Marker, 
        caller: Option<SegmentId>, 
        scope_chain: Vec<Marker>,
        handled_marker: Marker,
    ) -> Self {
        Segment {
            marker,
            frames: Vec::new(),
            caller,
            scope_chain,
            kind: SegmentKind::PromptBoundary { handled_marker },
        }
    }
    
    /// Push a frame (O(1) - adds to end)
    pub fn push_frame(&mut self, frame: Frame) {
        self.frames.push(frame);
    }
    
    /// Pop a frame (O(1) - removes from end)
    pub fn pop_frame(&mut self) -> Option<Frame> {
        self.frames.pop()
    }
    
    pub fn is_prompt_boundary(&self) -> bool {
        matches!(self.kind, SegmentKind::PromptBoundary { .. })
    }
    
    pub fn handled_marker(&self) -> Option<Marker> {
        match &self.kind {
            SegmentKind::PromptBoundary { handled_marker } => Some(*handled_marker),
            SegmentKind::Normal => None,
        }
    }
}
```

### Continuation (with Snapshot)

```rust
/// Captured or created continuation (subject to one-shot check).
/// 
/// Two kinds:
/// - Captured (started=true): frames_snapshot/scope_chain/marker/dispatch_id are valid
/// - Created (started=false): program/handlers are valid; frames_snapshot is empty
#[derive(Debug, Clone)]
pub struct Continuation {
    /// Unique identifier for one-shot tracking
    pub cont_id: ContId,
    
    /// Original segment this was captured from (for debugging/reference).
    /// Meaningful only when started=true.
    pub segment_id: SegmentId,
    
    /// Frozen frames at capture time (captured only)
    pub frames_snapshot: Arc<Vec<Frame>>,
    
    /// Frozen scope_chain at capture time (captured only)
    pub scope_chain: Arc<Vec<Marker>>,
    
    /// Handler marker this continuation belongs to (captured only).
    /// 
    /// SEMANTICS: This is the innermost handler at capture time (scope_chain[0]).
    /// Used primarily for debugging/tracing. The authoritative handler info
    /// is in scope_chain, not marker alone.
    /// 
    /// When Resume materializes, new segment gets marker = k.marker,
    /// but scope_chain is what actually determines which handlers are in scope.
    pub marker: Marker,
    
    /// Which dispatch created this (for completion detection).
    /// RULE: Only callsite continuations (k_user) have Some here.
    /// Handler-local continuations have None.
    pub dispatch_id: Option<DispatchId>,
    
    /// Whether this continuation is already started.
    /// started=true  => captured continuation
    /// started=false => created (unstarted) continuation
    pub started: bool,
    
    /// Program object to start when started=false (ProgramBase: KleisliProgramCall or EffectBase).
    pub program: Option<PyShared>,
    
    /// Handlers to install when started=false (innermost first).
    pub handlers: Vec<Handler>,

    /// [Q1] Preserved Rust handler sentinel identities (ADR-14).
    /// When a captured continuation round-trips through Python (via PyContinuation),
    /// Rust sentinel identity (pointer equality) would be lost. This field stores
    /// the original `Option<PyShared>` py_identity from each HandlerEntry in scope
    /// at capture time, allowing faithful restoration on resume.
    pub handler_identities: Vec<Option<PyShared>>,
}

impl Continuation {
    /// Capture a continuation from a segment.
    pub fn capture(
        segment: &Segment, 
        segment_id: SegmentId,
        dispatch_id: Option<DispatchId>,
    ) -> Self {
        Continuation {
            cont_id: ContId::fresh(),
            segment_id,
            frames_snapshot: Arc::new(segment.frames.clone()),
            scope_chain: Arc::new(segment.scope_chain.clone()),
            marker: segment.marker,
            dispatch_id,
            started: true,
            program: None,
            handlers: Vec::new(),
        }
    }
    
    /// Create an unstarted continuation from a program and handlers.
    pub fn create_unstarted(program: PyShared, handlers: Vec<Handler>) -> Self {
        Continuation {
            cont_id: ContId::fresh(),
            segment_id: SegmentId(0),  // unused when started=false
            frames_snapshot: Arc::new(Vec::new()),
            scope_chain: Arc::new(Vec::new()),
            marker: Marker(0),  // ignored when started=false
            dispatch_id: None,
            started: false,
            program: Some(program),
            handlers,
        }
    }
}
```

### DispatchContext [R11-E]

```rust
/// Tracks state of a specific effect dispatch.
#[derive(Debug, Clone)]
pub struct DispatchContext {
    /// Unique identifier
    pub dispatch_id: DispatchId,
    
    /// The effect being dispatched — opaque Python object. [R11-E]
    /// The VM does not inspect this. Handlers downcast as needed.
    /// DispatchEffect = PyShared in production; Effect enum in tests.
    pub effect: DispatchEffect,
    
    /// Snapshot of handler markers [innermost, ..., outermost]
    pub handler_chain: Vec<Marker>,
    
    /// Current position (0 = innermost)
    pub handler_idx: usize,
    
    /// Callsite continuation (for completion detection and Delegate)
    pub k_user: Continuation,
    
    /// Prompt boundary for the root handler of this dispatch.
    /// Used to detect handler return that abandons the callsite.
    pub prompt_seg_id: SegmentId,
    
    /// Marked true when callsite is resolved (Resume/Transfer/Return)
    pub completed: bool,
}
```

### Value (Python-Rust Interop)

```rust
/// A value that can flow through the VM.
/// 
/// Can be Rust-native, Python objects, or VM-level objects (Continuation/Handlers).
#[derive(Debug, Clone)]
pub enum Value {
    /// Python object (GIL-independent)
    Python(Py<PyAny>),

    /// Captured or created continuation
    Continuation(Continuation),

    /// Handler list (innermost first)
    Handlers(Vec<Handler>),

    /// Task handle (see SPEC-SCHED-001)
    Task(Py<PyAny>),

    /// Promise handle (see SPEC-SCHED-001)
    Promise(Py<PyAny>),

    /// External promise handle (see SPEC-SCHED-001)
    ExternalPromise(Py<PyAny>),
    
    /// Rust unit (for primitives that don't return meaningful values)
    Unit,
    
    /// Rust integer (optimization for common case)
    Int(i64),
    
    /// Rust string (optimization for common case)
    String(String),
    
    /// Rust boolean
    Bool(bool),
    
    /// None/null
    None,
    
    /// [D8] Call stack metadata (returned by GetCallStack)
    CallStack(Vec<CallMetadata>),
    
    /// [D11] List of values (e.g. Gather results)
    List(Vec<Value>),
}

impl Value {
    /// Convert to Python object (requires GIL)
    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        match self {
            Value::Python(obj) => Ok(obj.bind(py).clone()),
            Value::Continuation(k) => k.to_pyobject(py),
            Value::Handlers(handlers) => {
                let py_list = PyList::empty(py);
                for h in handlers {
                    py_list.append(h.to_pyobject(py)?)?;
                }
                Ok(py_list.into_any())
            }
            Value::Task(obj) | Value::Promise(obj) | Value::ExternalPromise(obj)
                => Ok(obj.bind(py).clone()),
            Value::Unit => Ok(py.None().into_bound(py)),
            Value::Int(i) => Ok(i.into_pyobject(py)?.into_any()),
            Value::String(s) => Ok(s.into_pyobject(py)?.into_any()),
            Value::Bool(b) => Ok(b.into_pyobject(py)?.into_any()),
            Value::None => Ok(py.None().into_bound(py)),
        }
    }
    
    /// Create from Python object (requires GIL)
    pub fn from_pyobject(obj: &Bound<'_, PyAny>) -> Self {
        // Check None first
        if obj.is_none() {
            return Value::None;
        }
        // Check bool before int (bool is subclass of int in Python)
        if let Ok(b) = obj.extract::<bool>() {
            return Value::Bool(b);
        }
        if let Ok(i) = obj.extract::<i64>() {
            return Value::Int(i);
        }
        if let Ok(s) = obj.extract::<String>() {
            return Value::String(s);
        }
        Value::Python(obj.clone().unbind())
    }
}
```

### Effect Types — `#[pyclass]` Structs [R11-A]

Effects are data. The VM does not interpret them — it passes them opaquely
through dispatch to the handler that claims them. Effects that Rust handlers
need to inspect are `#[pyclass(frozen)]` structs defined in Rust and exposed
to Python. User-defined effects are plain Python classes (subclassing
`EffectBase`). Both flow through the same dispatch path identically.

**There is no `Effect` enum.** The VM sees effects as `Py<PyAny>`. [R11-B]

#### Standard Effects (State / Reader / Writer)

```rust
/// State effect: read a key.
/// Python: `yield Get("counter")`
#[pyclass(frozen, name = "Get")]
pub struct PyGet {
    #[pyo3(get)] pub key: String,
}

/// State effect: write a key.
/// Python: `yield Put("counter", 42)`
#[pyclass(frozen, name = "Put")]
pub struct PyPut {
    #[pyo3(get)] pub key: String,
    #[pyo3(get)] pub value: PyObject,
}

/// State effect: modify a key with a function.
/// Python: `yield Modify("counter", lambda x: x + 1)`
#[pyclass(frozen, name = "Modify")]
pub struct PyModify {
    #[pyo3(get)] pub key: String,
    #[pyo3(get)] pub func: PyObject,
}

/// Reader effect: read from environment.
/// Python: `yield Ask("database_url")`
#[pyclass(frozen, name = "Ask")]
pub struct PyAsk {
    #[pyo3(get)] pub key: String,
}

/// Writer effect: append to log.
/// Python: `yield Tell("Starting operation")`
#[pyclass(frozen, name = "Tell")]
pub struct PyTell {
    #[pyo3(get)] pub message: PyObject,
}
```

#### Scheduler Effects

> Scheduler effect types (Spawn, Wait, Gather, Race, Cancel, CreatePromise,
> CompletePromise, FailPromise, CreateExternalPromise, SchedulerYield,
> TaskCompleted), user-facing handle types (Task, Future, Promise,
> ExternalPromise, RaceResult), Waitable protocol, and TaskCancelledError
> are defined in **SPEC-SCHED-001**.

#### User-defined effects

User effects are plain Python classes. They do NOT need to be `#[pyclass]`.
They subclass `EffectBase` (Python) and flow through dispatch as `Py<PyAny>`
like everything else. Python handlers receive them and read attributes
with normal Python attribute access.

```python
class MyDatabaseQuery(EffectBase):
    def __init__(self, sql: str):
        self.sql = sql

# Handler (Python):
def db_handler(effect, k):
    if isinstance(effect, MyDatabaseQuery):
        result = execute_sql(effect.sql)
        yield Resume(k, result)
    else:
        yield Pass()
```

### Dispatch Base Classes — Rust `#[pyclass(subclass)]` [R11-F] [R13-D]

Any type hierarchy used for type-based dispatching in `classify_yielded`
MUST have its base class defined as a Rust `#[pyclass(subclass)]`. This
makes `isinstance` checks a C-level pointer comparison instead of Python
module imports + getattr + MRO walks.

**Explicit Perform hierarchy [R14-A]**: `DoExpr` is control IR only. Effects are
`EffectValue` data and are resolved via `DoCtrl::Perform(effect)`.

**GIL-free type dispatch [R13-I]**: All DoExpr nodes carry an immutable `tag: DoExprTag`
discriminant set at construction. The VM reads this tag to classify and downcast
yielded values **without GIL** — it's a Rust field read on `#[pyclass(frozen)]` data.
No `is_instance_of`, no `PyType_IsSubtype`, no MRO walk.

```rust
/// [R13-I] Discriminant tag for GIL-free type dispatch.
/// Set once at construction, immutable (frozen pyclass).
/// VM reads this to classify DoExpr nodes in the step loop without GIL.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DoExprTag {
    // === DoCtrl variants (VM instructions) ===
    Pure        = 0,
    Call        = 1,
    Map         = 2,
    FlatMap     = 3,
    WithHandler = 4,
    Resume      = 5,
    Transfer    = 6,
    Delegate    = 7,
    Eval        = 8,
    // ...
    Pass        = 19,   // [R15-A] terminal pass-through (old Delegate semantics)
    GetHandlers = 9,
    GetCallStack = 10,
    GetContinuation = 11,
    CreateContinuation = 12,
    ResumeContinuation = 13,
    PythonAsyncSyntaxEscape = 14,

    // === Effect (handler-dispatched) ===
    // All effects share a single tag value. The VM doesn't distinguish
    // between effect types — it dispatches all of them to handlers.
    Effect      = 128,

    // === Unknown (error) ===
    Unknown     = 255,
}

impl DoExprTag {
    #[inline]
    pub fn is_do_ctrl(self) -> bool { (self as u8) < 128 }
    #[inline]
    pub fn is_effect(self) -> bool { self as u8 == 128 }
}

/// Base for all effect types. [R11-F] [R13-I]
/// Python user effects subclass this. Rust effects use `extends=PyEffectBase`.
/// Tag is always DoExprTag::Effect. VM checks tag for GIL-free classification.
#[pyclass(subclass, frozen, name = "EffectBase")]
pub struct PyEffectBase {
    #[pyo3(get)]
    pub tag: u8,  // Always DoExprTag::Effect (128). Exposed to Python for introspection.
}

impl PyEffectBase {
    pub fn new() -> Self {
        Self { tag: DoExprTag::Effect as u8 }
    }
}

/// Base for all DoCtrl types. [R11-F] [R13-I]
/// WithHandler, Resume, Transfer, Delegate, Pure, Call, Map, FlatMap, etc. all extend this.
/// Each concrete subtype sets its specific DoExprTag at construction.
/// VM checks tag for GIL-free classification and variant dispatch.
#[pyclass(subclass, frozen, name = "DoCtrlBase")]
pub struct PyDoCtrlBase {
    #[pyo3(get)]
    pub tag: u8,  // Specific DoExprTag variant. Exposed to Python for introspection.
}

/// [R13-D] DELETED: DoThunkBase removed.
/// [R14-A] Effects are not DoExpr nodes; source-level `yield effect` lowers to
/// `yield Perform(effect)` before VM evaluation.
/// No `to_generator()` at VM level. Pure(callable) replaces DoThunk.
```

Concrete types extend their base and set the tag at construction:

```rust
// Effects — tag is always DoExprTag::Effect
#[pyclass(frozen, extends=PyEffectBase, name = "Get")]
pub struct PyGet { #[pyo3(get)] pub key: String }
// PyGet::new() calls PyEffectBase::new() for super → tag = 128

// DoCtrl primitives — each sets its specific tag
#[pyclass(frozen, extends=PyDoCtrlBase, name = "WithHandler")]
pub struct PyWithHandler { ... }
// super = PyDoCtrlBase { tag: DoExprTag::WithHandler as u8 }

#[pyclass(frozen, extends=PyDoCtrlBase, name = "Pure")]
pub struct PyPure { #[pyo3(get)] pub value: PyObject }
// super = PyDoCtrlBase { tag: DoExprTag::Pure as u8 }

#[pyclass(frozen, extends=PyDoCtrlBase, name = "Call")]
pub struct PyCall {
    #[pyo3(get)] pub f: PyObject,       // DoExpr — the callable
    #[pyo3(get)] pub args: Vec<PyObject>, // [DoExpr] — arguments
    #[pyo3(get)] pub kwargs: Option<PyObject>,
    #[pyo3(get)] pub metadata: PyObject,  // CallMetadata
}
// super = PyDoCtrlBase { tag: DoExprTag::Call as u8 }

// [R13-D] DoThunk types deleted — no PyPureProgram, PyDerivedProgram, etc.
// Pure(value) replaces them.
```

**GIL-free tag access [R13-I]**: The `tag` field is an immutable `u8` in a `frozen` pyclass.
To read it from `Py<PyAny>` without GIL, the VM uses unsafe pointer arithmetic to
reach the Rust struct data inside the Python object. Since the field is frozen (write-once
at construction, never mutated), this is safe:

```rust
/// [R13-I] Extract DoExprTag from any PyObject without GIL.
/// The tag field is at a known offset within the PyDoCtrlBase / PyEffectBase struct.
/// Since both bases store tag as their first field (u8), and all concrete DoExpr types
/// extend one of them, we can read the tag from the base class portion.
///
/// SAFETY: The object must be a DoExpr (DoCtrl or Effect). If it's an arbitrary Python
/// object that doesn't extend either base, this returns DoExprTag::Unknown.
/// The caller (classify_yielded) must handle Unknown gracefully.
#[inline]
unsafe fn read_do_expr_tag(obj: &Py<PyAny>) -> DoExprTag {
    // PyO3 stores #[pyclass] data at a fixed offset from the PyObject header.
    // For `extends=` types, the base class data comes first.
    // tag is the first (and only) field of PyDoCtrlBase / PyEffectBase.
    //
    // Implementation detail: use PyO3's internal AsPyPointer + offset calculation.
    // Actual offset depends on PyO3 version — abstract behind a helper.
    //
    // No fallback path is allowed.
    // If tag offset cannot be determined, this is a hard error in VM setup.
    // Classification MUST remain tag-based and GIL-free.
    let tag_byte: u8 = /* read from known offset */;
    DoExprTag::try_from(tag_byte).unwrap_or(DoExprTag::Unknown)
}
```

Python user-defined types extend the same bases:

```python
from doeff_vm import EffectBase, DoCtrlBase

# User effect — isinstance(obj, EffectBase) works
class MyDatabaseQuery(EffectBase):
    def __init__(self, sql: str):
        self.sql = sql

# [R13-D] DoThunkBase deleted — no third base.
# User code yields DoCtrl or Effect nodes directly.
# To create a callable DoExpr, use Pure(callable) or Call(...).
```

With tag-based dispatch [R13-I], `classify_yielded` is **GIL-free** — it reads
the `tag` field directly from the frozen pyclass struct:

```rust
/// [R13-I] GIL-free classification. Reads tag from frozen pyclass data.
/// Called from VM step loop WITHOUT GIL held.
fn classify_yielded(obj: &Py<PyAny>) -> Yielded {
    // SAFETY: tag is an immutable u8 in a frozen pyclass. Read-only access is safe.
    let tag = unsafe { read_do_expr_tag(obj) };
    if tag.is_do_ctrl() {
        Yielded::DoCtrl { tag, obj: obj.clone() }
    } else if tag.is_effect() {
        Yielded::Effect(obj.clone())
    } else {
        // strict mode: this is a contract violation and must become a clear Python TypeError
        return classify_type_error("yielded value is not DoExpr")
    }
}
```

And `handle_do_ctrl` dispatches on the tag to extract variant-specific fields.
Field extraction (reading `.value`, `.f`, `.args`, etc.) from frozen pyclasses
is also GIL-free — these are immutable Rust struct fields:

```rust
/// [R13-I] GIL-free field extraction from DoCtrl variants.
/// tag has already been read by classify_yielded. Now extract variant-specific data.
fn extract_do_ctrl(tag: DoExprTag, obj: &Py<PyAny>) -> DoCtrl {
    match tag {
        DoExprTag::Pure => {
            // SAFETY: frozen PyPure { value: PyObject } — read immutable field
            let value = unsafe { read_field::<PyPure>(obj, |p| p.value.clone()) };
            DoCtrl::Pure { value }
        }
        DoExprTag::Call => {
            let (f, args, kwargs, metadata) = unsafe {
                read_field::<PyCall>(obj, |c| (c.f.clone(), c.args.clone(), c.kwargs.clone(), c.metadata.clone()))
            };
            DoCtrl::Call { f, args, kwargs, metadata }
        }
        // ... other variants
        _ => unreachable!("tag already validated as is_do_ctrl()")
    }
}
```

No GIL. No Python imports. No getattr. No string matching. No MRO walk.
The tag read + field extraction is pure Rust memory access on immutable data.
No fallback path is permitted in strict mode.
Error signaling for this boundary should surface as Python exceptions whenever possible.

The old `Effect` enum (`Get { key }`, `Put { key, value }`, etc.) is deleted.
`effect.rs` becomes a module defining the `#[pyclass]` structs above.

### Python FFI Wrappers (DoCtrl primitives, Continuation, Handler) [R8-C]

DoCtrl primitives and the continuation handle are Rust `#[pyclass]` types
exposed to Python (see ADR-9). Effects are ALSO `#[pyclass]` types but
defined in the Effect Types section above — they are data, not VM primitives.

```rust
/// Opaque continuation handle passed to Python handlers. [R8-C]
/// Python code can pass K around but cannot inspect its internals.
#[pyclass]
pub struct K {
    // Internal: cont_id, looked up in VM continuation registry
    cont_id: ContId,
}

/// Composition primitive — usable in any Program. [R8-C]
#[pyclass]
pub struct WithHandler {
    #[pyo3(get)] pub handler: PyObject,
    #[pyo3(get)] pub expr: PyObject,
}

/// Dispatch primitive — handler-only, during effect handling. [R8-C]
#[pyclass]
pub struct Resume {
    #[pyo3(get)] pub continuation: PyObject,  // K instance
    #[pyo3(get)] pub value: PyObject,
}

/// Dispatch primitive — handler-only, non-terminal re-perform. [R8-C, R15-A]
/// Handler receives the result back via K_new continuation swap.
#[pyclass]
pub struct Delegate {
    #[pyo3(get)] pub effect: Option<PyObject>,  // None = use current dispatch effect
}

/// Dispatch primitive — handler-only, terminal pass-through. [R15-A]
/// Handler gives up control entirely — identical to pre-R15 Delegate semantics.
#[pyclass]
pub struct Pass {
    #[pyo3(get)] pub effect: Option<PyObject>,  // None = use current dispatch effect
}

/// Dispatch primitive — handler-only, one-shot. [R8-C]
#[pyclass]
pub struct Transfer {
    #[pyo3(get)] pub continuation: PyObject,  // K instance
    #[pyo3(get)] pub value: PyObject,
}
```

FFI conversions for VM-internal types:

```rust
impl Continuation {
    /// Convert to Python object (driver only, requires GIL).
    /// Returns a K instance (opaque handle).
    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>>;

    /// Convert from Python object (driver only, requires GIL).
    /// Accepts K instances, extracts cont_id.
    pub fn from_pyobject(obj: &Bound<'_, PyAny>) -> PyResult<Self>;
}

impl Handler {
    /// Convert to Python object (driver only, requires GIL). [R8-D]
    /// Returns the py_identity stored in HandlerEntry — preserving the
    /// original Python object the user passed to run() or WithHandler.
    pub fn to_pyobject<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>>;

    /// Convert from Python object (driver only, requires GIL).
    /// Recognizes Rust sentinel objects (state/reader/writer/scheduler)
    /// and wraps them as Handler::RustProgram. All others become Handler::Python.
    pub fn from_pyobject(obj: &Bound<'_, PyAny>) -> PyResult<Self>;
}
```

**Note**: Effect types no longer need `to_pyobject` / `from_pyobject` conversions.
They ARE Python objects (`#[pyclass]`). The VM passes them through as `Py<PyAny>`
without conversion. When a Python handler receives an effect, it gets the
original `#[pyclass]` instance — isinstance works, attribute access works.

### Handler (RustProgram + Python) [R8-G] [R11-D]

```rust
/// A handler that can process effects.
///
/// Handlers are installed via WithHandler and matched during dispatch.
/// Two implementation strategies, one dispatch protocol.
#[derive(Debug, Clone)]
pub enum Handler {
    /// Rust-native handler (generator-like protocol).
    /// Used by state, reader, writer, scheduler, and any custom Rust handler.
    RustProgram(RustProgramHandlerRef),

    /// Python handler function.
    /// Signature: def handler(effect, k) -> Program[Any]
    Python(PyShared),
}

/// Shared reference to a Rust program handler factory.
pub type RustProgramHandlerRef = Arc<dyn RustProgramHandler + Send + Sync>;

/// Shared reference to a running Rust handler program (cloneable for continuations).
pub type RustProgramRef = Arc<Mutex<Box<dyn RustHandlerProgram + Send>>>;

/// Result of stepping a Rust handler program.
pub enum RustProgramStep {
    /// Yield a DoCtrl / effect / program
    Yield(Yielded),
    /// Return a value (like generator return)
    Return(Value),
    /// Throw an exception into the VM
    Throw(PyException),
    /// [R8-H] Need to call a Python function (e.g., Modify calling modifier).
    /// The program is suspended; result feeds back via resume().
    NeedsPython(PythonCall),
}

/// A Rust handler program instance (generator-like). [R11-D]
///
/// start/resume/throw mirror Python generator protocol but run in Rust.
/// `start()` receives the effect as opaque `Py<PyAny>`. The handler
/// downcasts to the concrete #[pyclass] type it knows how to handle
/// (e.g., `obj.downcast::<PyGet>()`) using Python::with_gil().
pub trait RustHandlerProgram {
    fn start(&mut self, py: Python<'_>, effect: &Bound<'_, PyAny>,
             k: Continuation, store: &mut RustStore) -> RustProgramStep;
    fn resume(&mut self, value: Value, store: &mut RustStore) -> RustProgramStep;
    fn throw(&mut self, exc: PyException, store: &mut RustStore) -> RustProgramStep;
}

/// Factory for Rust handler programs. [R11-D]
///
/// Each dispatch creates a fresh RustHandlerProgram instance.
/// `can_handle()` receives the effect as opaque `Bound<'_, PyAny>`.
/// The factory decides via isinstance whether it handles this effect.
pub trait RustProgramHandler {
    fn can_handle(&self, py: Python<'_>, effect: &Bound<'_, PyAny>) -> bool;
    fn create_program(&self) -> RustProgramRef;
}

impl Handler {
    /// Check if this handler can handle the given effect. [R11-D]
    pub fn can_handle(&self, py: Python<'_>, effect: &Bound<'_, PyAny>) -> bool {
        match self {
            Handler::RustProgram(handler) => handler.can_handle(py, effect),
            Handler::Python(_) => {
                // Python handlers are considered capable of handling any effect.
                // They yield Pass() for effects they don't handle.
                true
            }
        }
    }
}

/// [SUPERSEDED BY R15-A / SPEC-KPC-001 — KPC is now a call-time macro, not a runtime effect.
/// KPC handler is removed. `KleisliProgram.__call__()` returns `Call` DoCtrl directly.
/// The following pseudo-code is retained for historical reference only.]
///
/// [Q2] KPC Handler: dispatches KleisliProgramCall effects.
///
/// Installed as the innermost handler by run(). When a KPC effect is yielded,
/// the handler resolves args via Eval (for lazy KPC arguments), then starts
/// the kleisli_source program with the resolved args.
///
/// This handler is required for @do-decorated programs to function, since
/// @do produces KleisliProgramCall effects that must be caught and executed.
pub struct KpcHandlerFactory; // [SUPERSEDED BY R15-A / SPEC-KPC-001]

impl RustProgramHandler for KpcHandlerFactory { // [SUPERSEDED BY R15-A / SPEC-KPC-001]
    fn can_handle(&self, py: Python<'_>, effect: &Bound<'_, PyAny>) -> bool {
        // Handles KPC effects (PyKPC or objects with __doeff_kpc__ marker)
        effect.is_instance_of::<PyKPC>()
    }
    fn create_program(&self) -> RustProgramRef { /* KpcHandlerProgram */ } // [SUPERSEDED]
}

/// [SUPERSEDED] KpcHandlerProgram resolves KPC arg values (some may be lazy DoExprs),
/// then calls the execution_kernel with resolved args. Multi-phase:
/// 1. start(): yield GetHandlers to capture current handler stack
/// 2. If lazy args (KpcArg::Expr): yield DoCtrl::Eval for each, resume() collects
/// 3. All args resolved: yield DoCtrl::Call { f: kernel, args, kwargs }
///    VM calls kernel(*args, **kwargs) → generator → pushed as frame [R12-A]
/// 4. When kernel generator completes: yield DoCtrl::Resume { k_user, value }
pub struct KpcHandlerProgram { // [SUPERSEDED BY R15-A / SPEC-KPC-001]
    // Internal state machine for arg resolution phases
}
```

### Handler Entry with Identity Preservation [R8-D]

```rust
/// Entry in the handler table, linking a Handler to its prompt segment
/// and preserving the original Python object for GetHandlers.
#[derive(Debug, Clone)]
pub struct HandlerEntry {
    pub handler: Handler,
    pub prompt_seg_id: SegmentId,
    /// Original Python object passed by the user.
    /// Returned by GetHandlers to preserve id()-level identity.
    pub py_identity: Option<PyShared>,
}

/// [Q8] Python-visible wrapper for Rust handler sentinels.
///
/// Rust handler factories (StateHandlerFactory, ReaderHandlerFactory, etc.) are
/// not directly visible to Python. PyRustHandlerSentinel wraps them as #[pyclass]
/// objects that Python code can pass to run(handlers=[...]).
///
/// Each sentinel preserves identity: `state is state` is True (same Python object).
/// This is critical for ADR-14 handler identity preservation — GetHandlers returns
/// the original sentinel objects so Python code can compare handlers by identity.
#[pyclass(frozen)]
pub struct PyRustHandlerSentinel {
    factory: Box<dyn RustProgramHandler>,
    // Identity preserved through Python object reference
}
```

### Standard Handlers as RustProgramHandler [R8-H] [R8-I] [R11-D]

The standard handlers (state, reader, writer) implement the same
`RustProgramHandler` trait as the scheduler. Handlers receive the effect
as opaque `&Bound<'_, PyAny>` and downcast to the concrete `#[pyclass]`
types they know how to handle. The VM never extracts effect fields.

```rust
/// State handler factory. Handles PyGet, PyPut, PyModify.
/// Backed by RustStore.state.
#[derive(Debug, Clone)]
pub struct StateHandlerFactory;

impl RustProgramHandler for StateHandlerFactory {
    fn can_handle(&self, _py: Python<'_>, effect: &Bound<'_, PyAny>) -> bool {
        effect.is_instance_of::<PyGet>()
            || effect.is_instance_of::<PyPut>()
            || effect.is_instance_of::<PyModify>()
    }
    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(StateHandlerProgram::new())))
    }
}

/// State handler program instance.
///
/// Get/Put: start() handles immediately, yields Resume(k, value).
/// Modify:  start() reads old value, yields the modifier call as a
///          sub-program; resume() receives new value, stores it,
///          yields Resume(k, old_value).
struct StateHandlerProgram { /* state machine fields */ }

impl RustHandlerProgram for StateHandlerProgram {
    fn start(&mut self, py: Python<'_>, effect: &Bound<'_, PyAny>,
             k: Continuation, store: &mut RustStore) -> RustProgramStep
    {
        // Downcast to concrete #[pyclass] types — the handler knows its types.
        if let Ok(get) = effect.downcast::<PyGet>() {
            let key = &get.borrow().key;
            let value = store.get(key).cloned().unwrap_or(Value::None);
            return RustProgramStep::Yield(Yielded::DoCtrl(
                DoCtrl::Resume { continuation: k, value }
            ));
        }
        if let Ok(put) = effect.downcast::<PyPut>() {
            let b = put.borrow();
            let value = Value::from_pyobject(b.value.bind(py));
            store.put(b.key.clone(), value);
            return RustProgramStep::Yield(Yielded::DoCtrl(
                DoCtrl::Resume { continuation: k, value: Value::Unit }
            ));
        }
        if let Ok(modify) = effect.downcast::<PyModify>() {
            let b = modify.borrow();
            self.pending_key = Some(b.key.clone());
            self.pending_k = Some(k);
            let old_value = store.get(&b.key).cloned().unwrap_or(Value::None);
            self.pending_old_value = Some(old_value.clone());
            // [R8-H] Need Python call: modifier(old_value).
            return RustProgramStep::NeedsPython(PythonCall::CallFunc {
                func: PyShared::new(b.func.clone_ref(py)),
                args: vec![old_value],
                kwargs: vec![],
            });
        }
        // Unknown effect — delegate to next handler
        RustProgramStep::Yield(Yielded::DoCtrl(
            DoCtrl::Delegate { effect: PyShared::new(effect.clone().unbind()) }
        ))
    }

    fn resume(&mut self, value: Value, store: &mut RustStore) -> RustProgramStep {
        // Called after Modify's modifier(old_value) returns
        let key = self.pending_key.take().unwrap();
        let k = self.pending_k.take().unwrap();
        let old_value = self.pending_old_value.take().unwrap();
        store.put(key, value);  // value = new_value from modifier
        RustProgramStep::Yield(Yielded::DoCtrl(
            DoCtrl::Resume { continuation: k, value: old_value }
        ))
    }

    fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> RustProgramStep {
        RustProgramStep::Throw(exc)
    }
}

/// Reader handler factory. Handles PyAsk.
/// Backed by RustStore.env.
#[derive(Debug, Clone)]
pub struct ReaderHandlerFactory;

impl RustProgramHandler for ReaderHandlerFactory {
    fn can_handle(&self, _py: Python<'_>, effect: &Bound<'_, PyAny>) -> bool {
        effect.is_instance_of::<PyAsk>()
    }
    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(ReaderHandlerProgram)))
    }
}

struct ReaderHandlerProgram;

impl RustHandlerProgram for ReaderHandlerProgram {
    fn start(&mut self, py: Python<'_>, effect: &Bound<'_, PyAny>,
             k: Continuation, store: &mut RustStore) -> RustProgramStep
    {
        if let Ok(ask) = effect.downcast::<PyAsk>() {
            let key = &ask.borrow().key;
            let value = store.ask(key).cloned().unwrap_or(Value::None);
            return RustProgramStep::Yield(Yielded::DoCtrl(
                DoCtrl::Resume { continuation: k, value }
            ));
        }
        RustProgramStep::Yield(Yielded::DoCtrl(
            DoCtrl::Delegate { effect: PyShared::new(effect.clone().unbind()) }
        ))
    }
    fn resume(&mut self, _: Value, _: &mut RustStore) -> RustProgramStep {
        unreachable!("ReaderHandler never yields mid-handling")
    }
    fn throw(&mut self, exc: PyException, _: &mut RustStore) -> RustProgramStep {
        RustProgramStep::Throw(exc)
    }
}

/// Writer handler factory. Handles PyTell.
/// Backed by RustStore.log.
#[derive(Debug, Clone)]
pub struct WriterHandlerFactory;

impl RustProgramHandler for WriterHandlerFactory {
    fn can_handle(&self, _py: Python<'_>, effect: &Bound<'_, PyAny>) -> bool {
        effect.is_instance_of::<PyTell>()
    }
    fn create_program(&self) -> RustProgramRef {
        Arc::new(Mutex::new(Box::new(WriterHandlerProgram)))
    }
}

struct WriterHandlerProgram;

impl RustHandlerProgram for WriterHandlerProgram {
    fn start(&mut self, py: Python<'_>, effect: &Bound<'_, PyAny>,
             k: Continuation, store: &mut RustStore) -> RustProgramStep
    {
        if let Ok(tell) = effect.downcast::<PyTell>() {
            let message = Value::from_pyobject(tell.borrow().message.bind(py));
            store.tell(message);
            return RustProgramStep::Yield(Yielded::DoCtrl(
                DoCtrl::Resume { continuation: k, value: Value::Unit }
            ));
        }
        RustProgramStep::Yield(Yielded::DoCtrl(
            DoCtrl::Delegate { effect: PyShared::new(effect.clone().unbind()) }
        ))
    }
    fn resume(&mut self, _: Value, _: &mut RustStore) -> RustProgramStep {
        unreachable!("WriterHandler never yields mid-handling")
    }
    fn throw(&mut self, exc: PyException, _: &mut RustStore) -> RustProgramStep {
        RustProgramStep::Throw(exc)
    }
}
```

### Built-in Scheduler Handler

> See **SPEC-SCHED-001** for the complete scheduler specification.

The built-in scheduler is a `RustProgramHandler` that handles concurrency effects
(Spawn, Wait, Gather, Race, Cancel, Promise, ExternalPromise). It is **not**
auto-installed; users install it explicitly via `WithHandler`. It can be replaced
by custom Python or Rust handlers. All types, state machine, effect handling, and
implementation details are defined in SPEC-SCHED-001.

### Python API for Standard Handlers [R8-H]

Users install standard handlers via `run()` or `WithHandler` (see SPEC-009):

```python
from doeff import run, WithHandler
from doeff.handlers import state, reader, writer

# Install standard handlers explicitly
result = run(
    user_program(),
    handlers=[state, reader, writer],
    store={"x": 0},
    env={"key": "val"},
)

# Observe state after execution
print(result.raw_store)    # Dict of state key-value pairs

# Users can replace standard handlers with custom ones
@do
def my_persistent_state(effect, k):
    if isinstance(effect, Get):
        value = db.get(effect.key)
        result = yield Resume(k, value)
        return result
    elif isinstance(effect, Put):
        db.put(effect.key, effect.value)
        result = yield Resume(k, None)
        return result
    else:
        yield Pass()

# Custom handler intercepts state effects instead of standard state handler
result = run(
    user_program(),
    handlers=[my_persistent_state, reader, writer],
    env={"key": "val"},
)
```

### PythonCall and PendingPython (Purpose-Tagged Calls)

**CRITICAL**: When VM returns `NeedsPython`, it must also store `pending_python` 
to know what to do with the result. Different call types have different result handling.

**GIL RULE**: The driver converts Python objects to `Value` before returning
`PyCallOutcome::Value` to the VM. The VM never calls `Value::from_pyobject`.

**ASYNC RULE**: `PythonAsyncSyntaxEscape` maps to `PythonCall::CallAsync` and
`PendingPython::AsyncEscape`. Only async_run may execute CallAsync; sync_run errors.

```rust
/// A pending call into Python code.
/// 
/// IMPORTANT: Generators are NOT callables. This enum correctly
/// distinguishes between calling functions and advancing generators.
#[derive(Debug, Clone)]
pub enum PythonCall {
    /// [R13-E] Evaluate a DoExpr node (Pure(callable), Call(...), Effect, etc.).
    /// For Pure(callable): driver calls callable() and expects a generator.
    /// For Call nodes: driver evaluates args first, then calls.
    /// For effects: this path is not used (effects go through dispatch).
    EvalDoExpr {
        expr: Py<PyAny>,
    },
    
    /// Call a Python function for pure computation (non-program).
    CallFunc {
        func: Py<PyAny>,
        args: Vec<Value>,
    },
    
    /// Call a Python function that returns an awaitable (async_run only).
    /// Driver awaits the result and returns PyCallOutcome::Value.
    CallAsync {
        func: Py<PyAny>,
        args: Vec<Value>,
    },
    
    /// Call a Python handler with effect and continuation.
    /// Driver wraps Effect/Continuation into Python objects (PyEffect/PyContinuation).
    /// Handler must return a generator (DoExpr evaluation result).
    CallHandler {
        handler: Py<PyAny>,
        effect: Effect,
        continuation: Continuation,
    },
    
    /// Start a generator (first iteration, equivalent to __next__)
    /// [C2-fix] Generator is NOT carried here — it lives in PendingPython::StepUserGenerator.
    /// Driver retrieves it from pending_python when executing the call.
    GenNext,

    /// Send a value to a running generator
    /// [C2-fix] Generator retrieved from PendingPython, not from this variant.
    GenSend {
        value: Value,
    },

    /// Throw an exception into a generator
    /// [C2-fix] Generator retrieved from PendingPython, not from this variant.
    GenThrow {
        exc: PyException,
    },
}

/// What to do when Python call returns.
/// 
/// INVARIANT: When step() returns NeedsPython, VM.pending_python is set.
/// When receive_python_result() is called, VM uses pending_python to route the result.
#[derive(Debug, Clone)]
pub enum PendingPython {
    /// [R13-E] EvalDoExpr for a DoExpr node - result is Value::Python(generator).
    /// Carries optional CallMetadata to attach to the PythonGenerator frame.
    /// When metadata is Some, the frame was created via DoCtrl::Call.
    /// When metadata is None, the frame is a WithHandler body (no call context).
    EvalDoExprFrame {
        metadata: Option<CallMetadata>,
    },
    
    /// GenNext/GenSend/GenThrow on a user generator frame
    /// On GenYield: re-push generator with started=true and preserved metadata [R9-C]
    /// On GenReturn/GenError: generator is done, don't re-push
    StepUserGenerator {
        /// The generator being stepped (needed for re-push)
        generator: Py<PyAny>,
        /// CallMetadata from the original frame (preserved across yields) [R9-C]
        metadata: Option<CallMetadata>,
    },
    
    /// CallHandler for Python handler invocation
    /// Result is Value::Python(generator) after evaluating handler's returned DoExpr. [R13-E]
    /// The resulting generator is treated as a handler program; StopIteration
    /// triggers implicit handler return semantics.
    CallPythonHandler {
        /// Continuation to pass to handler
        k_user: Continuation,
        /// Effect being handled
        effect: Effect,
    },
    
    /// [R8-H] RustProgram handler needs Python callback (e.g., Modify calling modifier function).
    /// The handler's RustHandlerProgram is suspended; result feeds back via resume().
    RustProgramContinuation {
        /// Handler marker (to locate handler in scope_chain)
        marker: Marker,
        /// Continuation from the dispatch context
        k: Continuation,
    },

    /// PythonAsyncSyntaxEscape awaiting (async_run only)
    AsyncEscape,

    /// [R13-B] Map(source, f) pending — source evaluated, need to apply f
    MapPending {
        /// Mapping function (Python callable)
        f: Py<PyAny>,
    },

    /// [R13-B] FlatMap(source, binder) pending — source evaluated, need to apply binder
    FlatMapPending {
        /// Binder function (Python callable returning DoExpr)
        binder: Py<PyAny>,
    },

    /// [R13-B] FlatMap binder called — waiting for binder(source) result.
    /// Result is a DoExpr to evaluate.
    FlatMapBinderResult,

    /// [R13-C] Call(f, args, kwargs, meta) — evaluating f or args[i] as DoExpr.
    /// Tracks progress through the sequential left-to-right evaluation.
    /// Phase 1: Evaluate f → get callable. Phase 2: Evaluate args[0..n] → get values.
    /// Phase 3: Invoke callable(*resolved_args, **kwargs).
    CallEvalProgress {
        /// Phase of evaluation: EvalF, EvalArg(index), or Invoke.
        phase: CallEvalPhase,
        /// The callable (set after f is evaluated). None during EvalF phase.
        resolved_f: Option<Py<PyAny>>,
        /// Remaining unevaluated arg DoExprs (consumed left-to-right)
        remaining_args: Vec<Py<PyAny>>,
        /// Already-resolved arg values (in order)
        resolved_args: Vec<Py<PyAny>>,
        /// kwargs (passed through unchanged — not DoExprs, just Python dict)
        kwargs: Option<Py<PyAny>>,
        /// Call metadata for stack traces
        metadata: CallMetadata,
    },
}

/// [R13-C] Tracks which phase of Call arg evaluation we're in.
#[derive(Debug, Clone)]
pub enum CallEvalPhase {
    /// Evaluating `f` DoExpr — waiting for the callable
    EvalF,
    /// Evaluating `args[index]` — waiting for the value
    EvalArg { index: usize },
    /// All args resolved, ready to invoke
    Invoke,
}

**DoExpr Input Rule [R14-A]**: DoExpr is control IR. Effects are data lifted by
`Perform(effect)` at lowering/boundary. Generators yield DoCtrl nodes. The VM
evaluates them. `Call(f: DoExpr, args: [DoExpr], kwargs, meta)`
evaluates `f` and each arg, then calls `f(*resolved_args, **resolved_kwargs)`.
`Pure(value)` evaluates to `value` immediately. `Map(source, f)` and `FlatMap(source, binder)`
are functor/monad operations. No `to_generator()` at VM level — that's a Python API detail.
```

### Program Frame Re-Push Rule (Python + Rust)

**CRITICAL INVARIANT**: When stepping a Python generator or a Rust handler program
and it yields (not returns/errors), the program frame must be re-pushed to the
current segment.

```
GenNext/GenSend/GenThrow → driver executes → PyCallOutcome::GenYield(yielded)
  ↓
receive_python_result:
  1. Re-push generator as Frame::PythonGenerator { generator, started: true, metadata }
     (metadata is preserved from the original frame — it does not change across yields)
  2. Set mode = HandleYield(yielded)
  
GenReturn/GenError → generator is DONE, do NOT re-push
  ↓
receive_python_result:
  1. Do NOT push any frame (generator consumed)
  2. Set mode = Deliver(value) or Throw(exception)
```

Rust program step:

```
RustProgramStep::Yield(yielded)
  ↓
apply_rust_program_step:
  1. Re-push Frame::RustProgram { program }
  2. Set mode = HandleYield(yielded)

RustProgramStep::Return/Throw → program is DONE, do NOT re-push
  ↓
apply_rust_program_step:
  1. Do NOT push any frame (program consumed)
  2. Set mode = Deliver(value) or Throw(exception)
```

This ensures the program frame exists when we need to send the next value.

**Handler Return Hook**: When pushing a handler program frame (Python or Rust),
the VM also installs a handler-return hook (e.g., a RustReturn callback or a
special frame). When the handler returns (StopIteration/Return), the hook runs
`handle_handler_return(value)` so implicit Return semantics apply. User programs
do not install this hook.

---

## VM State (3-Layer Model)

The VM state is organized into three layers with clear separation of concerns:

| Layer | Name | Contents | Visibility |
|-------|------|----------|------------|
| **1** | `Internals` | dispatch_stack, consumed_ids, segments, callbacks | **NEVER** exposed to users |
| **2** | `RustStore` | state, env, log (standard handler data) | User-observable via `RunResult.raw_store` |
| **3** | `PyStore` | Python dict (optional) | User-owned free zone |

### Design Principles

1. **Internals are sacred**: Control flow structures that could break VM invariants are hidden
2. **RustStore is the source of truth**: Standard handlers read/write here; fast Rust access
3. **PyStore is an escape hatch**: Python handlers can store arbitrary data; VM doesn't read it
4. **No synchronization**: RustStore and PyStore are independent; no mirroring or sync
5. **Continuations don't snapshot S**: State is global (no backtracking by default).
   Spawned tasks get an isolated RustStore snapshot by default (SPEC-SCHED-001).
   PyStore remains shared unless a GIL-aware copy path is added.

### Layer 1: Internals (VM-internal, invisible to users)

Layer 1 fields are defined directly in the VM struct (see "VM Struct" below).
They include: `segments`, `free_segments`, `dispatch_stack`, `callbacks`, 
`consumed_cont_ids`, `handlers`.

These structures maintain VM invariants and must NOT be accessible or 
modifiable by user code directly.

### Layer 2: RustStore (user-space, Rust HashMap)

```rust
/// Standard handler state. Rust-native for performance.
///
/// This is the "main memory" for standard effects (Get/Put/Ask/Tell).
/// Python handlers can access via PyO3-exposed read/write APIs.
/// 
/// Key design: Value can hold Py<PyAny>, so Python objects flow through.
/// SPEC-SCHED-001: isolated store requires RustStore to be cloneable.
#[derive(Clone)]
pub struct RustStore {
    /// State for Get/Put/Modify effects
    pub state: HashMap<String, Value>,
    
    /// Environment for Ask/Local effects
    pub env: HashMap<String, Value>,
    
    /// Log for Tell/Listen effects
    pub log: Vec<Value>,
    
    // Future: cache, metrics, etc.
}

impl RustStore {
    pub fn new() -> Self {
        RustStore {
            state: HashMap::new(),
            env: HashMap::new(),
            log: Vec::new(),
        }
    }
    
    // === State operations (used by StateHandlerFactory) ===
    
    pub fn get(&self, key: &str) -> Option<&Value> {
        self.state.get(key)
    }
    
    pub fn put(&mut self, key: String, value: Value) {
        self.state.insert(key, value);
    }
    
    pub fn modify<F>(&mut self, key: &str, f: F) -> Option<Value> 
    where F: FnOnce(&Value) -> Value 
    {
        self.state.get(key).map(|old| {
            let new = f(old);
            let old_clone = old.clone();
            self.state.insert(key.to_string(), new);
            old_clone
        })
    }
    
    // === Environment operations (used by ReaderHandlerFactory) ===
    
    pub fn ask(&self, key: &str) -> Option<&Value> {
        self.env.get(key)
    }
    
    pub fn with_local<F, R>(&mut self, bindings: HashMap<String, Value>, f: F) -> R
    where F: FnOnce(&mut Self) -> R
    {
        let old: HashMap<String, Value> = bindings.keys()
            .filter_map(|k| self.env.get(k).map(|v| (k.clone(), v.clone())))
            .collect();
        // [C4-fix] Track keys that are NEW (not overwriting existing entries)
        let new_keys: Vec<String> = bindings.keys()
            .filter(|k| !old.contains_key(*k))
            .cloned()
            .collect();

        // Apply new bindings
        for (k, v) in bindings {
            self.env.insert(k, v);
        }

        let result = f(self);

        // Restore old bindings
        for (k, v) in old {
            self.env.insert(k, v);
        }
        // [C4-fix] Remove keys that were added (didn't exist before)
        for k in new_keys {
            self.env.remove(&k);
        }

        result
    }
    
    // === Log operations (used by WriterHandlerFactory) ===
    
    pub fn tell(&mut self, message: Value) {
        self.log.push(message);
    }
    
    pub fn logs(&self) -> &[Value] {
        &self.log
    }
    
    pub fn clear_logs(&mut self) -> Vec<Value> {
        std::mem::take(&mut self.log)
    }
}
```

### Layer 3: PyStore (user-space, Python dict, optional)

```rust
/// Optional Python dict for user-defined handler state.
/// 
/// This is a "free zone" - VM doesn't read it, users can do anything.
/// Use cases:
/// - Python custom handlers storing arbitrary info
/// - Debug/tracing metadata
/// - Prototyping before solidifying Rust key model
/// 
/// NOTE: No synchronization with RustStore. They are independent.
/// PyStore is shared across tasks even when RustStore is isolated.
pub struct PyStore {
    dict: Py<PyDict>,
}

impl PyStore {
    pub fn new(py: Python<'_>) -> Self {
        PyStore {
            dict: PyDict::new(py).unbind(),
        }
    }
    
    /// Get the underlying Python dict (for Python handlers)
    pub fn as_dict<'py>(&self, py: Python<'py>) -> &Bound<'py, PyDict> {
        self.dict.bind(py)
    }
}
```

### VM Struct (Unified Definition)

**Note**: Level 1 and Level 2 are logical subsystems; implementation is a single mode-based VM.

**Non-normative memory note**: The concrete arena/free-list strategy (`segments` +
`free_segments`) is an implementation detail. The normative requirement is semantic
ownership/lifetime safety for segments and continuations, not a specific allocator shape.

```rust
/// The algebraic effects VM.
/// 
/// Single unified struct combining all three state layers.
/// The step() function is the single execution entry point.
pub struct VM {
    // === Layer 1: Internals (invisible to users) ===
    
    /// Segment arena (owns all segments)
    segments: Vec<Segment>,
    
    /// Free list for segment reuse
    free_segments: Vec<SegmentId>,
    
    /// Dispatch stack (tracks effect dispatch in progress)
    dispatch_stack: Vec<DispatchContext>,
    
    /// Callback table for FnOnce (Frame::RustReturn references these)
    callbacks: SlotMap<CallbackId, Callback>,
    
    /// One-shot tracking for continuations
    consumed_cont_ids: HashSet<ContId>,
    
    /// Handler registry: marker -> HandlerEntry
    /// NOTE: Includes prompt_seg_id to avoid linear search
    handlers: HashMap<Marker, HandlerEntry>,

    /// [Q6] Continuation registry for Python-side K lookup.
    /// Maps ContId to Continuation so that PyContinuation objects (exposed to
    /// Python handlers via K) can look up the actual Rust Continuation.
    /// Entries are removed on consumption (one-shot enforcement).
    continuation_registry: HashMap<ContId, Continuation>,
    
    // === Layer 2: RustStore (user-observable via RunResult.raw_store) ===

    /// Standard handler state (State/Reader/Writer handlers use this)
    pub rust_store: RustStore,
    
    // === Layer 3: PyStore (optional escape hatch) ===
    
    /// User Python dict for custom handler state
    py_store: Option<PyStore>,
    
    // === Execution State ===
    
    /// Current segment being executed
    current_segment: SegmentId,
    
    /// Current execution mode (state machine)
    mode: Mode,
    
    /// Pending Python call context (set when NeedsPython returned).
    /// INVARIANT: Some when step() returned NeedsPython, None otherwise.
    /// Used by receive_python_result() to know what to do with result.
    pending_python: Option<PendingPython>,

    /// Debug configuration (off by default).
    debug: DebugConfig,
    
    /// Monotonic step counter for debug output.
    step_counter: u64,
    
    /// [D7] Registry of all continuations created during execution.
    /// Keyed by ContId, used for one-shot enforcement and cleanup.
    continuation_registry: HashMap<ContId, Continuation>,
}

/// Handler registry entry.
/// 
/// Includes prompt_seg_id to avoid linear search during dispatch.
/// Created by WithHandler, looked up by start_dispatch.
#[derive(Debug, Clone)]
pub struct HandlerEntry {
    /// The handler implementation
    pub handler: Handler,
    
    /// Prompt segment for this handler (set at WithHandler time)
    /// Abandon/return goes here. No search needed.
    pub prompt_seg_id: SegmentId,
    
    /// [D8] Original Python object passed by the user.
    /// Returned by GetHandlers to preserve id()-level identity.
    pub py_identity: Option<PyShared>,
}
```

### Debug Mode (Step Tracing)

Debug mode prints useful runtime state while stepping. It is **off by default**
and must not call into Python (no GIL usage in debug output).

```rust
#[derive(Debug, Clone)]
pub enum DebugLevel {
    Off,
    Steps,  // One-line summary per step
    Trace,  // Includes handler/dispatch/yield details
}

#[derive(Debug, Clone)]
pub struct DebugConfig {
    pub level: DebugLevel,
    pub show_frames: bool,
    pub show_dispatch: bool,
    pub show_store: bool,
}
```

**Step output (Steps)**:
- step_id, mode kind, current_segment, frames_len
- dispatch_stack depth, pending_python kind

**Additional output (Trace)**:
- top frame kind (RustReturn/RustProgram/PythonGenerator)
- effect type_name when handling Yielded::Effect
- handler_idx and handler chain length (if dispatch active)
- continuation ids when Resume/Transfer/Delegate is applied

**Python values**: printed as placeholders (e.g., `<pyobject>`) to avoid GIL.

**Integration**:
- `VM::step()` increments `step_counter` and emits a debug line before/after state transitions.
- `receive_python_result()` may emit a debug line showing the PyCallOutcome kind.
- The driver may optionally emit Python-level debug info (with GIL) if requested.

### Python API for Debug

```python
vm = doeff.VM(debug=True)  # Steps level
vm.set_debug(DebugConfig(level="trace", show_frames=True, show_dispatch=True))
result = vm.run(program)
```

Debug output defaults to stderr.

---

## Step State Machine

The VM executes via a mode-based state machine. Each `step()` call transitions the mode exactly once.

### StepEvent (External Interface)

`step()` returns one of these events to the driver (PyO3 wrapper):

```rust
/// Result of a single VM step.
/// 
/// The driver loop calls step() repeatedly until Done or Error.
/// When NeedsPython is returned, driver executes Python call and feeds result back.
pub enum StepEvent {
    /// Internal transition occurred; keep stepping (pure Rust)
    Continue,
    
    /// Need to call into Python (GIL boundary)
    NeedsPython(PythonCall),
    
    /// Computation completed successfully
    Done(Value),
    
    /// Computation failed
    Error(VMError),
}
```

**Note**: `Continue` means the VM made progress internally. The value being delivered is stored in `VM.mode`, not returned. This simplifies the state machine.

**Async note**: `PythonCall::CallAsync` is only valid under `async_run` / `VM.run_async`.
The sync driver must raise `TypeError` if it receives CallAsync.

### Mode (Internal State)

```rust
/// VM's internal execution mode.
/// 
/// Each step() transitions mode exactly once.
pub enum Mode {
    /// Deliver a value to the next frame
    Deliver(Value),
    
    /// Throw an exception to the next frame
    Throw(PyException),
    
    /// Handle something yielded by a generator or Rust program
    HandleYield(Yielded),
    
    /// Current segment is empty; return value to caller
    Return(Value),
}
```

### Yielded (Generator Output Classification)

**IMPORTANT**: Classification of Python generator yields happens in the **driver**
(with GIL), not in the VM. Rust program handlers yield `Yielded` directly.
The VM receives pre-classified `Yielded` values and operates without GIL.

```rust
/// Classification of what a generator yielded. [R11-B] [R11-C]
/// 
/// INVARIANT: Python generator yields are classified by the DRIVER (GIL held),
/// not by the VM. Rust program handlers return Yielded directly.
/// The VM receives Yielded and processes it without needing GIL.
pub enum Yielded {
    /// A DoCtrl (Resume, Transfer, WithHandler, Call, GetCallStack, etc.)
    DoCtrl(DoCtrl),
    
    /// An effect to be dispatched — opaque Python object. [R11-B]
    /// The VM does not inspect this. It passes it through start_dispatch()
    /// to the handler. The handler downcasts as needed.
    Effect(Py<PyAny>),
    
    /// Unknown object (will cause TypeError)
    Unknown(Py<PyAny>),
}

impl Yielded {
    /// Classify a Python object yielded by a generator. [R11-C] [R13-D] [R13-I]
    /// 
    /// GIL-FREE. Reads the tag field from the frozen pyclass struct.
    /// Can be called from the driver (with GIL) or from the VM step loop (without GIL).
    ///
    /// [R14-A] Classification output is DoCtrl only.
    /// EffectValue inputs are normalized to DoCtrl::Perform(effect).
    /// One tag read. No is_instance_of. No Python imports. No getattr.
    pub fn classify(obj: &Py<PyAny>) -> Self {
        // [R13-I] Read tag without GIL — frozen u8 field in pyclass struct
        let tag = unsafe { read_do_expr_tag(obj) };
        if tag.is_do_ctrl() {
            // Extract variant-specific fields GIL-free from frozen pyclass data
            let do_ctrl = extract_do_ctrl(tag, obj);
            Yielded::DoCtrl(do_ctrl)
        } else if tag.is_effect() {
            // [R14-B] Effects are data. Lift to explicit Perform control node.
            Yielded::DoCtrl(DoCtrl::Perform { effect: obj.clone() })
        } else {
            // [R13-D] No third category. Unknown → type error.
            Yielded::Unknown(obj.clone())
        }
    }
}

/// Extract CallMetadata from a Python program object (with GIL). [R9-E]
///
/// Returns Some(CallMetadata) if the object has recognizable metadata
/// (function_name, kleisli_source with __code__). Returns None otherwise.
fn extract_call_metadata(py: Python<'_>, obj: &Bound<'_, PyAny>) -> Option<CallMetadata> {
    let function_name = obj.getattr("function_name").ok()?.extract::<String>().ok()?;
    let (source_file, source_line) = if let Ok(kleisli) = obj.getattr("kleisli_source") {
        if let Ok(func) = kleisli.getattr("original_func") {
            if let Ok(code) = func.getattr("__code__") {
                let file = code.getattr("co_filename").ok()?.extract::<String>().ok()?;
                let line = code.getattr("co_firstlineno").ok()?.extract::<u32>().ok()?;
                (file, line)
            } else { return None; }
        } else { return None; }
    } else { return None; };
    Some(CallMetadata {
        function_name,
        source_file,
        source_line,
        program_call: Some(obj.clone().unbind()),
    })
}
```

**Key design points [R11] [R13]:**
- [R14-B] Effect values are lifted to `Yielded::DoCtrl(Perform { effect })`.
- `Yielded::Program` is deleted. [R13-D: DoThunk eliminated — binary hierarchy.]
- `classify` does ONE isinstance check for effects — no per-type arms.
- The classifier NEVER reads effect fields (`.key`, `.value`, `.items`, etc.).
- Rust program handlers yield `Yielded` directly (already classified),
  so no driver-side classification or GIL is required for those yields.
- [R14-A] Classification output is DoCtrl-only; effect resolution is explicit via Perform.

`extract_control_primitive` uses `Handler::from_pyobject` to decode `WithHandler`
and `CreateContinuation` handler arguments, and `Continuation::from_pyobject`
to decode `Resume`/`Transfer`/`ResumeContinuation`.
It also recognizes `PythonAsyncSyntaxEscape` and extracts the `action` callable.

### PyCallOutcome (Python Call Results)

**CRITICAL**: EvalDoExpr/CallFunc/CallAsync/CallHandler and Gen* have different semantics:
- `EvalDoExpr` returns a **Value** (Value::Python(generator) for Pure(callable)) [R13-E]
- `CallFunc` returns a **Value** (non-generator result)
- `CallAsync` returns a **Value** (awaited result; async_run only)
- `CallHandler` returns a **Value** (Value::Python(generator))
- `GenNext/GenSend/GenThrow` interact with a running generator (yield/return/error)

```rust
/// Result of executing a PythonCall.
/// 
/// IMPORTANT: This enum correctly separates:
/// - EvalDoExpr/CallFunc/CallAsync/CallHandler results (a Value) [R13-E]
/// - Generator step results (yield/return/error)
pub enum PyCallOutcome {
    /// EvalDoExpr returns Value::Python(generator) for Pure(callable). [R13-E]
    /// CallFunc returns Value (non-generator).
    /// CallAsync returns Value (awaited result).
    /// CallHandler returns Value::Python(generator).
    /// VM should push Frame::PythonGenerator with started=false and metadata for generator Values.
    /// The driver performs Python->Value conversion while holding the GIL.
    Value(Value),
    
    /// Generator yielded a value.
    /// Driver has already classified it (requires GIL).
    GenYield(Yielded),
    
    /// Generator returned via StopIteration.
    GenReturn(Value),
    
    /// Generator (or EvalDoExpr/CallFunc/CallAsync/CallHandler) raised an exception. [R13-E]
    GenError(PyException),
}

/// Wrapper for Python exceptions in Rust.
/// [Q5] Enum with lazy variants for GIL-free exception creation.
/// `Materialized` holds a captured Python exception triple (from GIL context).
/// `RuntimeError`/`TypeError` hold only a message string — the actual Python
/// exception object is created lazily when materialized on the Python side.
/// This allows the VM to create and propagate exceptions without holding the GIL.
#[derive(Debug, Clone)]
pub enum PyException {
    /// Captured from a live Python exception (has GIL-independent PyShared refs).
    Materialized {
        exc_type: PyShared,
        exc_value: PyShared,
        exc_tb: Option<PyShared>,
    },
    /// Lazy RuntimeError — message only, no GIL needed to construct.
    RuntimeError {
        message: String,
    },
    /// Lazy TypeError — message only, no GIL needed to construct.
    TypeError {
        message: String,
    },
}

impl PyException {
    pub fn runtime_error(message: String) -> Self { PyException::RuntimeError { message } }
    pub fn type_error(message: String) -> Self { PyException::TypeError { message } }
}
```

**Key insight**: `GenYield(Yielded)` contains a *classified* `Yielded`, not a raw `Py<PyAny>`. 
Classification and Python->Value conversion require GIL, so driver does them. VM receives
pre-classified data and `Value` only, and stays GIL-free.

---

## Mode Transitions

### Overview

```
                    ┌─────────────────────────────────────────┐
                    │              VM.step()                   │
                    │                                         │
   ┌────────────────┼─────────────────────────────────────────┼────────────────┐
   │                │                                         │                │
   ▼                ▼                                         ▼                ▼
Deliver(v)      Throw(e)                              HandleYield(y)      Return(v)
   │                │                                         │                │
   │                │                                         │  (y already    │
   ▼                ▼                                         │   classified   │
frames.pop()   frames.pop()                                   │   by driver or Rust)│
   │                │                                         │                │
   ├─RustReturn─────┼──────────────────────────────────┬──────┘                │
   │  callback(v)   │  callback(e)                     │                       │
   ├─RustProg───────┼──────────────────────────────────┤                       │
   │  step()/yield  │                                  │                       │
    │                │                                  ├─DoCtrl───────────────►│
    ├─PyGen──────────┼──────────────────────────────────┤  handle_do_ctrl()     │
    │  NeedsPython   │  NeedsPython(GenThrow)           │  [R13-D: binary]      │
    │  (GenSend/Next)│                                  │                       │
    │                │                                  ├─Effect───────────────►│
    ▼                ▼                                  │  start_dispatch()     │
                                                        │  (all effects)        │
                                                        │                       │
                                                        └─Unknown──────────────►│
                                                           Throw(TypeError)     │
                                                                               │
                                                                               ▼
                                                                        ┌──────────┐
                                                                        │ Yes: goto│
                                                                        │  caller  │
                                                                        │ segment  │
                                                                        ├──────────┤
                                                                        │ No: Done │
                                                                        │  or Err  │
                                                                        └──────────┘
```

### Rule 1: Deliver(value) / Throw(exception)

```rust
fn step_deliver_or_throw(&mut self) -> StepEvent {
    let segment = &mut self.segments[self.current_segment.index()];
    
    // If segment has no frames, transition to Return
    if segment.frames.is_empty() {
        match &self.mode {
            Mode::Deliver(v) => self.mode = Mode::Return(v.clone()),
            Mode::Throw(e) => {
                // Exception with no handler - propagate up
                if let Some(caller_id) = segment.caller {
                    self.current_segment = caller_id;
                    // mode stays Throw
                    return StepEvent::Continue;
                } else {
                    return StepEvent::Error(VMError::UncaughtException(e.clone()));
                }
            }
            _ => unreachable!(),
        }
        return StepEvent::Continue;
    }
    
    // Pop frame (O(1) from end)
    let frame = segment.frames.pop().unwrap();
    
    match frame {
        Frame::RustReturn { cb } => {
            // Consume callback and execute
            let callback = self.callbacks.remove(cb)
                .expect("callback must exist");
            
            match &self.mode {
                Mode::Deliver(v) => {
                    // Callback returns new Mode
                    self.mode = callback(v.clone(), self);
                    StepEvent::Continue
                }
                Mode::Throw(e) => {
                    // Rust callbacks don't handle exceptions; propagate
                    self.mode = Mode::Throw(e.clone());
                    StepEvent::Continue
                }
                _ => unreachable!(),
            }
        }
        
        Frame::RustProgram { program } => {
            let step = match &self.mode {
                Mode::Deliver(v) => {
                    let mut guard = program.lock().expect("Rust program lock poisoned");
                    guard.resume(v.clone(), &mut self.rust_store)
                }
                Mode::Throw(e) => {
                    let mut guard = program.lock().expect("Rust program lock poisoned");
                    guard.throw(e.clone(), &mut self.rust_store)
                }
                _ => unreachable!(),
            };
            self.apply_rust_program_step(step, program)
        }
        
        Frame::PythonGenerator { generator, started, metadata } => {
            // Need to call Python
            // CRITICAL: Set pending_python so receive_python_result knows to re-push
            // [R9-C] metadata is preserved across yields (carried in StepUserGenerator)
            self.pending_python = Some(PendingPython::StepUserGenerator {
                generator: generator.clone(),
                metadata: metadata.clone(),  // Carry metadata for re-push [R9-C]
            });
            
            // [C2-fix] Generator is stored in PendingPython::StepUserGenerator (set above),
            // not carried in the PythonCall variant. Driver retrieves it from pending_python.
            match &self.mode {
                Mode::Deliver(v) => {
                    if started {
                        StepEvent::NeedsPython(PythonCall::GenSend {
                            value: v.clone(),
                        })
                    } else {
                        // First call uses GenNext
                        StepEvent::NeedsPython(PythonCall::GenNext)
                    }
                }
                Mode::Throw(exc) => {
                    StepEvent::NeedsPython(PythonCall::GenThrow {
                        exc,
                    })
                }
                _ => unreachable!(),
            }
        }
    }
}
```

### Rule 2: Receive Python Result → Route Based on PendingPython

```rust
impl VM {
    /// Called by driver after executing PythonCall.
    /// 
    /// Uses pending_python to know what to do with the result.
    /// INVARIANT: pending_python is Some when this is called.
    /// Driver has already converted Python objects to Value.
    pub fn receive_python_result(&mut self, outcome: PyCallOutcome) {
        let pending = self.pending_python.take()
            .expect("pending_python must be set when receiving result");
        
        match (pending, outcome) {
            // === EvalDoExprFrame: EvalDoExpr returned Value::Python(generator) === [R13-E]
            (PendingPython::EvalDoExprFrame { metadata }, PyCallOutcome::Value(Value::Python(gen_obj))) => {
                // Push generator as new frame with started=false and CallMetadata [R9-G]
                let segment = &mut self.segments[self.current_segment.index()];
                segment.push_frame(Frame::PythonGenerator {
                    generator: gen_obj,
                    started: false,
                    metadata,  // Some for DoCtrl::Call, None for WithHandler body
                });
                // Mode stays Deliver (will trigger GenNext on next step)
            }
            (PendingPython::EvalDoExprFrame { .. }, PyCallOutcome::Value(_)) => {
                self.mode = Mode::Throw(PyException::type_error(
                    "DoExpr did not return a generator"
                ));
            }
            (PendingPython::EvalDoExprFrame { .. }, PyCallOutcome::GenError(e)) => {
                // EvalDoExpr raised exception [R13-E]
                self.mode = Mode::Throw(e);
            }
            
            // === StepUserGenerator: Generator stepped ===
            (PendingPython::StepUserGenerator { generator, metadata }, PyCallOutcome::GenYield(yielded)) => {
                // CRITICAL: Re-push generator with started=true + preserved metadata [R9-C]
                // Otherwise we lose the frame and can't continue it later
                let segment = &mut self.segments[self.current_segment.index()];
                segment.push_frame(Frame::PythonGenerator {
                    generator,
                    started: true,
                    metadata,  // Preserve call stack metadata across yields
                });
                self.mode = Mode::HandleYield(yielded);
            }
            (PendingPython::StepUserGenerator { .. }, PyCallOutcome::GenReturn(v)) => {
                // Generator completed - do NOT re-push
                // Value flows to next frame
                self.mode = Mode::Deliver(v);
            }
            (PendingPython::StepUserGenerator { .. }, PyCallOutcome::GenError(e)) => {
                // Generator raised exception - do NOT re-push
                self.mode = Mode::Throw(e);
            }
            
            // === CallPythonHandler: Handler returned Value::Python(generator) ===
            (PendingPython::CallPythonHandler { k_user, effect }, PyCallOutcome::Value(Value::Python(handler_gen))) => {
                // Handler returned a Program converted to a generator that yields primitives
                // Push handler-return hook (implicit Return), then generator frame (started=false)
                // Register handler-return callback for implicit handler return [C1-fix]
                let handler_return_cb = self.register_callback(Box::new(|value, vm| {
                    let _ = vm.handle_handler_return(value);
                    std::mem::replace(&mut vm.mode, Mode::Deliver(Value::Unit))
                }));
                let segment = &mut self.segments[self.current_segment.index()];
                segment.push_frame(Frame::RustReturn { cb: handler_return_cb });
                segment.push_frame(Frame::PythonGenerator {
                    generator: handler_gen,
                    started: false,
                });
                // k_user is stored in DispatchContext for completion detection and Delegate
            }
            (PendingPython::CallPythonHandler { .. }, PyCallOutcome::Value(_)) => {
                self.mode = Mode::Throw(PyException::type_error(
                    "handler did not return a generator (DoExpr evaluation result)"  // [R13-E]
                ));
            }
            (PendingPython::CallPythonHandler { .. }, PyCallOutcome::GenError(e)) => {
                self.mode = Mode::Throw(e);
            }
            
            // === [R8-H] RustProgramContinuation: RustProgram handler's Python call returned ===
            (PendingPython::RustProgramContinuation { marker, k }, PyCallOutcome::Value(result)) => {
                // Feed result back to the RustHandlerProgram via resume()
                // The handler program is located via marker in the scope_chain
                // and resumed with the Python call result as a Value
                self.mode = Mode::Deliver(result);
            }
            (PendingPython::RustProgramContinuation { .. }, PyCallOutcome::GenError(e)) => {
                self.mode = Mode::Throw(e);
            }
            
            // === AsyncEscape: PythonAsyncSyntaxEscape awaited ===
            (PendingPython::AsyncEscape, PyCallOutcome::Value(result)) => {
                self.mode = Mode::Deliver(result);
            }
            (PendingPython::AsyncEscape, PyCallOutcome::GenError(e)) => {
                self.mode = Mode::Throw(e);
            }
            
            // === [R13-B] MapPending: source evaluated, apply f ===
            (PendingPython::MapPending { f }, PyCallOutcome::Value(source_result)) => {
                // Source DoExpr evaluated. Now call f(source_result) in Python.
                self.pending_python = None;
                StepEvent::NeedsPython(PythonCall::CallFunc { func: f, args: vec![source_result] })
                // Driver returns Value — VM delivers it.
            }
            (PendingPython::MapPending { .. }, PyCallOutcome::GenError(e)) => {
                self.mode = Mode::Throw(e);
            }
            
            // === [R13-B] FlatMapPending: source evaluated, apply binder and evaluate result ===
            (PendingPython::FlatMapPending { binder }, PyCallOutcome::Value(source_result)) => {
                // Source DoExpr evaluated. Call binder(source_result) to get a new DoExpr.
                // Then evaluate the returned DoExpr. Two Python round-trips.
                self.pending_python = Some(PendingPython::FlatMapBinderResult);
                StepEvent::NeedsPython(PythonCall::CallFunc { func: binder, args: vec![source_result] })
                // Next: receive binder result → classify as DoExpr → evaluate it
            }
            (PendingPython::FlatMapBinderResult, PyCallOutcome::Value(binder_result)) => {
                // binder(source) returned a DoExpr. Classify and evaluate it.
                self.pending_python = None;
                let classified = Yielded::classify_pyobject(&binder_result);
                self.mode = Mode::HandleYield(classified);
                StepEvent::Continue
            }
            (PendingPython::FlatMapPending { .. }, PyCallOutcome::GenError(e)) |
            (PendingPython::FlatMapBinderResult, PyCallOutcome::GenError(e)) => {
                self.mode = Mode::Throw(e);
            }

            // === [R13-C] CallEvalProgress: evaluating f / args for Call ===
            (PendingPython::CallEvalProgress { phase, resolved_f, remaining_args, resolved_args, kwargs, metadata }, outcome) => {
                match (phase, outcome) {
                    // Phase 1: f evaluated — got the callable
                    (CallEvalPhase::EvalF, PyCallOutcome::Value(callable)) => {
                        if remaining_args.is_empty() {
                            // No args to evaluate — invoke immediately
                            self.pending_python = Some(PendingPython::EvalDoExprFrame {
                                metadata: Some(metadata),
                            });
                            StepEvent::NeedsPython(PythonCall::CallFunc {
                                func: callable,
                                args: resolved_args.into_iter().map(|v| Value::Python(v)).collect(),
                            })
                        } else {
                            // Start evaluating args[0]
                            let next_arg = remaining_args.remove(0);
                            self.pending_python = Some(PendingPython::CallEvalProgress {
                                phase: CallEvalPhase::EvalArg { index: 0 },
                                resolved_f: Some(callable),
                                remaining_args,
                                resolved_args,
                                kwargs,
                                metadata,
                            });
                            self.eval_do_expr(next_arg)
                        }
                    }
                    // Phase 2: args[i] evaluated — got the value
                    (CallEvalPhase::EvalArg { index }, PyCallOutcome::Value(arg_value)) => {
                        resolved_args.push(arg_value.to_pyobject());
                        if remaining_args.is_empty() {
                            // All args resolved — invoke callable(*resolved_args, **kwargs)
                            let f = resolved_f.expect("f must be resolved before args");
                            self.pending_python = Some(PendingPython::EvalDoExprFrame {
                                metadata: Some(metadata),
                            });
                            // The result of Call is a generator (the called program's lazy AST).
                            // Push it as Frame::PythonGenerator via EvalDoExprFrame routing.
                            StepEvent::NeedsPython(PythonCall::CallFunc {
                                func: f,
                                args: resolved_args.into_iter().map(|v| Value::Python(v)).collect(),
                            })
                        } else {
                            // More args to evaluate
                            let next_arg = remaining_args.remove(0);
                            self.pending_python = Some(PendingPython::CallEvalProgress {
                                phase: CallEvalPhase::EvalArg { index: index + 1 },
                                resolved_f,
                                remaining_args,
                                resolved_args,
                                kwargs,
                                metadata,
                            });
                            self.eval_do_expr(next_arg)
                        }
                    }
                    // Any phase: error
                    (_, PyCallOutcome::GenError(e)) => {
                        self.mode = Mode::Throw(e);
                    }
                    (phase, outcome) => {
                        panic!("Unexpected CallEvalProgress phase/outcome: {:?}/{:?}", phase, outcome);
                    }
                }
            }
            
            // Unexpected combinations
            (pending, outcome) => {
                panic!("Unexpected pending/outcome combination: {:?} / {:?}", pending, outcome);
            }
        }
    }
}
```

### Rule 3: HandleYield → Interpret Yielded Value

```rust
fn step_handle_yield(&mut self) -> StepEvent {
    let yielded = match std::mem::replace(&mut self.mode, Mode::Deliver(Value::Unit)) {
        Mode::HandleYield(y) => y,
        _ => unreachable!(),
    };
    
    match yielded {
        Yielded::DoCtrl(prim) => {
            // Handle DoCtrl
            self.handle_do_ctrl(prim)
        }
        
        Yielded::Effect(effect) => {
            // ALL effects go through dispatch — no bypass [R8-B] [R11-E]
            // effect is Py<PyAny> — opaque. The VM does not inspect it.
            match self.start_dispatch(py, effect) {
                Ok(event) => event,
                Err(e) => StepEvent::Error(e),
            }
        }
        
        Yielded::Unknown(obj) => {
            // Type error
            self.mode = Mode::Throw(PyException::type_error(
                format!("generator yielded unexpected type: {:?}", obj)
            ));
            StepEvent::Continue
        }
    }
}
```

### Rule 4: Return → Go to Caller or Complete

```rust
fn step_return(&mut self) -> StepEvent {
    let value = match std::mem::replace(&mut self.mode, Mode::Deliver(Value::Unit)) {
        Mode::Return(v) => v,
        _ => unreachable!(),
    };
    
    let segment = &self.segments[self.current_segment.index()];
    
    if let Some(caller_id) = segment.caller {
        // Switch to caller segment
        self.current_segment = caller_id;
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    } else {
        // No caller - computation complete
        StepEvent::Done(value)
    }
}
```

### Main Step Function

```rust
impl VM {
    /// Execute one step of the VM.
    pub fn step(&mut self) -> StepEvent {
        match &self.mode {
            Mode::Deliver(_) | Mode::Throw(_) => self.step_deliver_or_throw(),
            Mode::HandleYield(_) => self.step_handle_yield(),
            Mode::Return(_) => self.step_return(),
        }
    }
}
```

### Continuation Primitive Semantics (Summary)

- **GetContinuation**: returns the current dispatch callsite continuation (`k_user`) to the handler
  without consuming it. Error if called outside handler context.
- **GetHandlers**: returns the full handler chain from the callsite scope (innermost → outermost).
  These handlers can be passed back to `WithHandler` or `CreateContinuation`.
- **CreateContinuation**: returns an unstarted continuation storing `(expr, handlers)`.
- **ResumeContinuation**: if `started=true`, behaves like `Resume` (call-resume). If
  `started=false`, installs handlers (outermost first) and starts the program, returning
  to the current handler when it finishes; `value` is ignored.
- **Implicit Handler Return**: if a handler program (Python/Rust) returns, the VM
  treats it as handler return. For the root handler it abandons the callsite
  (marks dispatch completed) and returns to the prompt boundary. For inner
  handlers, return flows to the handler's caller segment; it does not flow back
  to a handler that already executed terminal `Pass(effect)`.
- **Single Resume per Dispatch**: The callsite continuation (`k_user`) is one-shot.
  Exactly one of Resume/Transfer/TransferThrow/Return may consume it in a
  dispatch. `yield Pass(effect)` is terminal — it does not return. Any
  double-resume is a runtime error.
- **No Multi-shot**: Multi-shot continuations are not supported. All continuations
  are one-shot and cannot be resumed more than once.

**Pass Data Flow (Terminal Pass-Through)** [R15-A]:

```
Pass is tail-call. The passing handler gives up control entirely.
k_user passes to the outer handler. The passing handler does NOT
receive a value back and cannot Resume.

User --perform E--> H1 (inner)
H1: yield Pass(E)               <- H1 is done, frames cleared
      |
      v
     H2 (outer) handles E
     H2: yield Resume(k_user, v)  <- resumes original callsite
     User: continues with v
     H2: return h2                <- flows to H2 caller (not back to H1)
```

**Delegate Data Flow (Non-Terminal Re-Perform)** [R15-B]:

```
Delegate is non-terminal. The delegating handler receives the result back
via K_new continuation swap. See SPEC-VM-010 for full mechanism.

User --perform E--> H1 (inner)
H1: raw = yield Delegate(E)     <- H1 suspends, VM captures K_new
      |
      v  (VM swaps DispatchContext.k_user = K_new)
     H2 (outer) handles E
     H2: yield Resume(K_new, v)   <- resumes K_new → sends v to H1
     H1: raw = v                  <- H1 receives v, transforms it
     H1: yield Resume(k_user, transform(v))  <- resumes original callsite
     User: continues with transform(v)
```

Notes:
- `yield Pass(E)` is terminal — code after it never executes.
- `yield Delegate(E)` is non-terminal — code after it receives the outer handler's result.
- If no outer handler matches, dispatch fails with a runtime error (both Pass and Delegate).

**Pass/Delegate/Resume Pseudocode** [R15-A]:

```python
@do
def user():
    x = yield SomeEffect()
    return x * 2

@do
def outer_handler(effect, k_user):
    if isinstance(effect, SomeEffect):
        user_ret = yield Resume(k_user, 10)
        return user_ret + 5
    yield Pass(effect)

# Pass is terminal — code after yield Pass() never executes.
@do
def passthrough_handler(effect, k_user):
    yield Pass(effect)
    # This line is UNREACHABLE — Pass does not return.

# Delegate is non-terminal — handler receives result back.
@do
def transforming_handler(effect, k_user):
    if isinstance(effect, SomeEffect):
        raw = yield Delegate()          # re-perform to outer handler
        # raw = value from outer handler's Resume
        transformed = raw * 2
        return (yield Resume(k_user, transformed))
    yield Pass()
```

## Perform from Handler Programs

When a Rust handler program yields `Perform(effect)`, the VM creates a new
dispatch. The handler's continuation becomes `k_user` in the new dispatch.
The handler retains the original callsite continuation and must eventually
resume it (or transfer-throw).

Flow:

```
User -> effect -> dispatch1 (k_user1 = user continuation)
  -> HandlerA.start(effect, k_user1)
    -> saves k_user1
    -> Perform(effect) -> dispatch2 (k_user2 = HandlerA continuation)
      -> HandlerB.start(effect, k_user2)
        -> Resume(k_user2, value)        <- resumes back to HandlerA
    -> HandlerA.resume(value)
      -> Resume(k_user1, processed_value) <- resumes back to user
```

Key properties:
- `Perform` is non-terminal: the handler frame is re-pushed and `resume()` is called.
- The handler must eventually `Resume(k_user1, value)` or `TransferThrow(k_user1, exc)`.
- If a handler performs but never resumes/transfers `k_user1`, the callsite is stuck.
- This is the canonical mechanism for handler-to-handler consultation.

Equivalent patterns:
- Koka: `val x = outer/op(); resume(f(x))`
- OCaml 5: `let x = perform Op in continue k (f x)`

---

## Driver Loop (PyO3 Side)

The driver handles GIL boundaries and **classifies yielded values** before passing to VM.
The sync driver is `run`; async integration is provided by `async_run` (see below).

```rust
impl PyVM {
    /// Run a program to completion.
    /// [R14-A] program is DoExpr control IR. Raw effect values are normalized to
    /// Perform(effect) at API/lowering boundary.
    /// For backward compatibility, if program is a ProgramBase with to_generator(),
    /// the DEPRECATED path extracts the generator. New code should yield DoExpr nodes.
    pub fn run(&mut self, py: Python<'_>, program: Bound<'_, PyAny>) -> PyResult<PyObject> {
        // [R14-A] Initialize: normalize/classify program as DoExpr control IR and start evaluation.
        // The program itself is the root DoExpr node. If it's a @do generator function,
        // calling it produces a generator — the generator IS the lazy AST.
        // Source-level `yield effect` is lowered to `yield Perform(effect)`.
        let classified = self.classify_program_input(py, &program)?;
        self.vm.start(classified);
        
        loop {
            // Release GIL for pure Rust steps
            let event = py.allow_threads(|| {
                loop {
                    match self.vm.step() {
                        StepEvent::Continue => continue,
                        other => return other,
                    }
                }
            });
            
            match event {
                StepEvent::Done(value) => {
                    return value.to_pyobject(py).map(|v| v.unbind());
                }
                
                StepEvent::Error(e) => {
                    return Err(e.to_pyerr(py));
                }
                
                StepEvent::NeedsPython(call) => {
                    let outcome = self.execute_python_call(py, call)?;
                    self.vm.receive_python_result(outcome);
                }
                
                StepEvent::Continue => unreachable!("handled in inner loop"),
            }
        }
    }
    
    /// Execute a Python call and return the outcome.
    /// 
    /// CRITICAL: This correctly distinguishes EvalDoExpr/CallFunc/CallAsync/CallHandler from Gen* results: [R13-E]
    /// - EvalDoExpr → Value::Python(generator) for Pure(callable)
    /// - CallFunc → Value (non-generator)
    /// - CallAsync → Value (awaited result; async_run only)
    /// - CallHandler → Value::Python(generator)
    /// - Gen* → GenYield/GenReturn/GenError (generator step result)
    /// 
    /// Classification of yielded values happens HERE (with GIL).
    fn execute_python_call(&self, py: Python<'_>, call: PythonCall) -> PyResult<PyCallOutcome> {
        match call {
            PythonCall::EvalDoExpr { expr } => {  // [R13-E]
                // For Pure(callable): extract callable and call it
                // For Call nodes: evaluate args first, then call
                // For effects: this path is not used (effects go through dispatch)
                // Simplified: assume expr is a callable for now (full impl would check DoCtrl type)
                if is_callable(py, &expr.bind(py)) {
                    let gen = expr.bind(py).call0()?;
                    Ok(PyCallOutcome::Value(Value::Python(gen.unbind())))
                } else {
                    Ok(PyCallOutcome::GenError(PyException::type_error(
                        "EvalDoExpr requires a callable DoExpr (Pure(callable) or similar)",
                    )))
                }
            }
            PythonCall::CallFunc { func, args } => {
                let py_args = args.to_py_tuple(py)?;
                match func.bind(py).call1(py_args) {
                    Ok(result) => {
                        // CallFunc returns a Value (not a generator yield!)
                        Ok(PyCallOutcome::Value(Value::from_pyobject(&result)))
                    }
                    Err(e) => {
                        Ok(PyCallOutcome::GenError(PyException::from_pyerr(py, e)))
                    }
                }
            }
            
            PythonCall::CallAsync { .. } => {
                Ok(PyCallOutcome::GenError(PyException::type_error(
                    "CallAsync requires async_run (PythonAsyncSyntaxEscape handler)",
                )))
            }
            
            PythonCall::CallHandler { handler, effect, continuation } => {
                // Wrap Effect/Continuation into Python objects while holding GIL
                let py_effect = effect.to_pyobject(py)?;
                let py_k = continuation.to_pyobject(py)?;
                match handler.bind(py).call1((py_effect, py_k)) {
                    Ok(result) => {
                        // Handler must return a generator (DoExpr evaluation result) [R13-E]
                        if is_generator(py, &result) {
                            Ok(PyCallOutcome::Value(Value::Python(result.unbind())))
                        } else {
                            Ok(PyCallOutcome::GenError(PyException::type_error(
                                "handler must return a generator (DoExpr evaluation result)",
                            )))
                        }
                    }
                    Err(e) => {
                        Ok(PyCallOutcome::GenError(PyException::from_pyerr(py, e)))
                    }
                }
            }
            
            PythonCall::GenNext => {
                let gen = self.pending_generator(py);
                self.step_generator(py, gen, "__next__", None)
            }

            PythonCall::GenSend { value } => {
                let gen = self.pending_generator(py);
                let py_value = value.to_pyobject(py)?;
                self.step_generator(py, gen, "send", Some(py_value))
            }

            PythonCall::GenThrow { exc } => {
                let gen = self.pending_generator(py);
                let exc_obj = exc.materialize(py);
                self.step_generator(py, gen, "throw", Some(exc_obj))
            }
        }
    }

    /// [R13-A] Classify program input into a form the VM can start evaluating.
    /// A @do-decorated function produces a generator when called — the generator IS the
    /// lazy AST. Each yield from it is a DoExpr node. The VM pushes it as a
    /// Frame::PythonGenerator and enters the step loop.
    /// For ProgramBase objects: call to_generator() (legacy compat) to get the generator.
    /// For raw generators: reject (use start_with_generator() explicitly).
    fn classify_program_input(
        &self,
        py: Python<'_>,
        program: &Bound<'_, PyAny>,
    ) -> PyResult<VMInput> {
        if program.is_instance_of::<PyGenerator>() {
            return Err(PyException::type_error(
                "DoExpr required; raw generators are not accepted. Use start_with_generator()."
            ).to_pyerr(py));
        }
        // Program objects expose to_generator() which returns the lazy AST generator.
        // This is a Python API convenience — the VM only sees generators that yield DoExpr nodes.
        let gen = if program.hasattr("to_generator")? {
            program.call_method0("to_generator")?
        } else if program.is_callable() {
            // Bare callable: call it to get the generator (e.g., @do function object)
            program.call0()?
        } else {
            return Err(PyException::type_error(
                "Program must be ProgramBase (with to_generator()) or a callable"
            ).to_pyerr(py));
        };
        Ok(VMInput::Generator(gen.unbind()))
    }

    /// [R13-E] DEPRECATED: to_generator() is a Python API detail, not a VM concept.
    /// The VM evaluates DoExpr nodes. For Pure(callable), the driver calls callable().
    /// This function may still exist for backward compatibility but is not part of
    /// the VM's core semantics.
    fn to_generator(
        &self,
        py: Python<'_>,
        program: Bound<'_, PyAny>,
    ) -> PyResult<Bound<'_, PyAny>> {
        // Legacy path — may be removed in future revisions
        if program.is_instance_of::<PyGenerator>()? {
            return Err(PyException::type_error(
                "DoExpr required; raw generators are not accepted"
            ).to_pyerr(py));
        }
        let to_gen = program.getattr("to_generator")?;
        to_gen.call0()
    }
    
    /// Step a generator and classify the result.
    /// 
    /// IMPORTANT: Classification happens HERE with GIL held.
    /// VM receives pre-classified Yielded and operates without GIL.
    fn step_generator(
        &self, 
        py: Python<'_>, 
        gen: Py<PyAny>, 
        method: &str, 
        arg: Option<Bound<'_, PyAny>>
    ) -> PyResult<PyCallOutcome> {
        let gen_bound = gen.bind(py);
        
        let result = match arg {
            Some(a) => gen_bound.call_method1(method, (a,)),
            None => gen_bound.call_method0(method),
        };
        
        match result {
            Ok(yielded_obj) => {
                // Generator yielded - classify GIL-free via tag read [R13-I]
                let classified = Yielded::classify(&yielded_obj.unbind());
                Ok(PyCallOutcome::GenYield(classified))
            }
            Err(e) if e.is_instance_of::<pyo3::exceptions::PyStopIteration>(py) => {
                // Generator completed
                let stop_iter = e.value(py);
                let return_value = stop_iter.getattr("value")?;
                Ok(PyCallOutcome::GenReturn(Value::from_pyobject(&return_value)))
            }
            Err(e) => {
                Ok(PyCallOutcome::GenError(PyException::from_pyerr(py, e)))
            }
        }
    }
}
```

---

## Asyncio Integration (Reference)

This section mirrors legacy SPEC-006's asyncio bridge [Deprecated], adapted to the Rust VM.
The VM core remains synchronous; async integration is implemented by a driver
wrapper and a handler that yields `PythonAsyncSyntaxEscape`.

### Async Driver (async_run)

`async_run` uses the same step loop but awaits `PythonCall::CallAsync` events.
All other PythonCall variants are handled synchronously via `execute_python_call`.

```python
async def async_run(vm, program):
    # [R13-A] classify_program_input extracts the generator (lazy AST) from program
    vm_input = vm.classify_program_input(program)
    vm.start(vm_input)
    while True:
        event = vm.step()
        if isinstance(event, Done):
            return event.value
        if isinstance(event, Error):
            raise event.error
        if isinstance(event, NeedsPython):
            call = event.call
            if isinstance(call, CallAsync):
                outcome = await execute_python_call_async(call)
            else:
                outcome = execute_python_call(call)
            vm.receive_python_result(outcome)
        await asyncio.sleep(0)
```

`execute_python_call_async` is a thin wrapper:

```python
async def execute_python_call_async(call):
    py_args = to_py_args(call.args)
    awaitable = call.func(*py_args)
    result = await awaitable
    return PyCallOutcome.Value(Value.from_pyobject(result))
```

Argument conversion uses the same `Value` → Python path as `CallFunc`.

### Await Effect (Reference)

`Await(awaitable)` is a Python-level effect (see SPEC-EFF-011). The Rust VM
treats it as `Effect::Python` and dispatches to user handlers.

Two reference handlers are provided:
- `sync_await_handler`: runs the awaitable in a background thread/executor and
  resumes the continuation with the result.
- `async_await_handler`: yields `PythonAsyncSyntaxEscape` so
  `async_run` can await in the event loop.

```python
@do
def sync_await_handler(effect, k):
    if isinstance(effect, Await):
        promise = yield CreateExternalPromise()
        thread_pool.submit(run_and_complete, effect.awaitable, promise)
        return (yield Wait(promise.future))
    yield Pass(effect)
```

```python
@do
def async_await_handler(effect, k):
    if isinstance(effect, Await):
        promise = yield CreateExternalPromise()
        async def fire_task():
            try:
                result = await effect.awaitable
                promise.complete(result)
            except BaseException as exc:
                promise.fail(exc)
        yield PythonAsyncSyntaxEscape(
            action=lambda: asyncio.create_task(fire_task())
        )
        return (yield Wait(promise.future))
    yield Pass(effect)
```

`async_await_handler` must only be used with `async_run`;
the sync driver raises `TypeError` if it sees `CallAsync`.

`run()` and `async_run()` must pass handler lists through unchanged. Handler
selection is user responsibility; no handler swapping or thread-offload
detection is allowed in VM wrappers.

**Usage**:
- Sync: `vm.run(with_handler(sync_await_handler, program))`
- Async: `await vm.run_async(with_handler(async_await_handler, program))`

---

## Public API Contract (SPEC-009 Support) [R8-J]

This section specifies the user-facing types and contracts that the VM must
expose to satisfy SPEC-009. Everything in this section is part of the
**public boundary** — the layer between user code and VM internals.

### run() and async_run() — Entrypoint Contract

```python
def run(
    program: Program[T],
    handlers: list[Handler] = [],
    env: dict[str, Any] = {},
    store: dict[str, Any] = {},
) -> RunResult[T]: ...

async def async_run(
    program: Program[T],
    handlers: list[Handler] = [],
    env: dict[str, Any] = {},
    store: dict[str, Any] = {},
) -> RunResult[T]: ...
```

These are **Python-side** functions that wrap `PyVM`. They are NOT methods on
PyVM — they create and configure a PyVM internally.

#### Implementation Contract

`run(program, handlers, env, store)` does the following in order:

```
1. Create PyVM instance
       vm = PyVM::new()

2. Initialize store (SPEC-009 API-6)
       for key, value in store.items():
           vm.put_state(key, Value::from_pyobject(value))

3. Initialize environment (SPEC-009 API-5)
       for key, value in env.items():
           vm.put_env(key, Value::from_pyobject(value))

4. Wrap program with handlers (nesting order — see below)
       wrapped = program
       for h in reversed(handlers):
           wrapped = WithHandler(handler=h, expr=wrapped)

5. Execute via driver loop
       final_value_or_error = vm.run(wrapped)   # driver loop from §Driver Loop

6. Extract results into RunResult
       raw_store = {k: v.to_pyobject() for k, v in vm.state_items()}
       result = Ok(final_value) or Err(exception)
       return RunResult(result=result, raw_store=raw_store)
```

`async_run` is identical except step 5 uses the async driver loop (§Async Driver).

#### Handler Nesting Order

`handlers=[h0, h1, h2]` produces:

```
WithHandler(h0,           ← outermost, sees effects LAST
  WithHandler(h1,
    WithHandler(h2,       ← innermost, sees effects FIRST
      program)))
```

`h2` is closest to the program — it sees effects first. `h0` is outermost —
it sees effects that `h1` and `h2` delegate. This matches `reversed(handlers)`.

**No default handlers** (SPEC-009 API-1). If `handlers=[]`, the program runs
with zero handlers. Yielding any effect raises `UnhandledEffect`.

#### NestingStep / NestingGenerator (ADR-13) [Q7]

The `WithHandler(h0, WithHandler(h1, WithHandler(h2, program)))` nesting is
implemented via a synthetic generator (`NestingGenerator`) that yields one
`WithHandler` DoCtrl at a time. The driver steps this generator through:

1. `run(program, handlers=[h0, h1, h2])` creates a `NestingGenerator` that
   will yield `WithHandler(h2, program)`, then `WithHandler(h1, ...)`,
   then `WithHandler(h0, ...)`.
2. Each `WithHandler` yield installs the handler and creates a new prompt
   segment. The NestingGenerator resumes with the next handler.
3. Once all handlers are installed, the innermost body (the user program)
   starts executing.

This avoids recursion or special-case nesting in the VM — the handler
installation loop is just normal generator stepping.

### RunResult — Execution Output [R8-J]

```rust
/// The public result of a run()/async_run() call.
///
/// This is a #[pyclass] exposed to Python. It is immutable (SPEC-009 API-7).
/// The concrete type is internal; users interact via the RunResult protocol.
#[pyclass(frozen)]
pub struct PyRunResult {
    /// Ok(value) or Err(exception)
    result: Result<Py<PyAny>, PyException>,
    /// Final store snapshot (extracted from RustStore at run completion)
    raw_store: Py<PyDict>,
}

#[pymethods]
impl PyRunResult {
    /// Ok(value) or Err(exception).
    #[getter]
    fn result(&self, py: Python<'_>) -> PyObject {
        match &self.result {
            Ok(v) => Ok(v.clone_ref(py)).to_pyobject(py),
            Err(e) => Err(e.clone()).to_pyobject(py),
        }
    }

    /// Final store snapshot after execution.
    #[getter]
    fn raw_store(&self, py: Python<'_>) -> PyObject {
        self.raw_store.clone_ref(py).into()
    }

    /// Unwrap Ok or raise the Err.
    #[getter]
    fn value(&self, py: Python<'_>) -> PyResult<PyObject> {
        match &self.result {
            Ok(v) => Ok(v.clone_ref(py)),
            Err(e) => Err(e.to_pyerr(py)),
        }
    }

    /// Get Err or raise ValueError if Ok.
    #[getter]
    fn error(&self, py: Python<'_>) -> PyResult<PyObject> {
        match &self.result {
            Err(e) => Ok(e.to_pyobject(py)),
            Ok(_) => Err(PyValueError::new_err("RunResult is Ok, not Err")),
        }
    }

    fn is_ok(&self) -> bool {
        self.result.is_ok()
    }

    fn is_err(&self) -> bool {
        self.result.is_err()
    }
}
```

**Construction** (inside `run()`/`async_run()` only):

```rust
/// Build RunResult after VM execution completes.
fn build_run_result(
    py: Python<'_>,
    vm: &VM,
    outcome: Result<Value, PyException>,
) -> PyResult<PyRunResult> {
    // Extract final store as Python dict (SPEC-009 API-6)
    let raw_store = PyDict::new(py);
    for (key, value) in vm.rust_store.state.iter() {
        raw_store.set_item(key, value.to_pyobject(py)?)?;
    }

    let result = match outcome {
        Ok(value) => Ok(value.to_pyobject(py)?.unbind()),
        Err(exc) => Err(exc),
    };

    Ok(PyRunResult {
        result,
        raw_store: raw_store.unbind(),
    })
}
```

**Invariants**:
- `raw_store` is always populated, even on error (SPEC-009 API-6).
  The store snapshot reflects state at the point execution stopped.
- `RunResult` is frozen/immutable (SPEC-009 API-7).
- `raw_store` contains only `state` entries (not `env` or `log`).
  Logs are accessible via `writer` handler if the user installed it.

### @do Decorator and Program[T] [R8-J]

`@do` is a **Python-side** decorator. It is NOT part of the Rust VM — it lives
in the `doeff` Python package. SPEC-008 defines how the VM processes its output.

#### What @do Does [R13-E]

```python
def do(fn):
    """Convert a generator function into a DoExpr factory.

    @do
    def counter(start: int):
        x = yield Get("count")
        yield Put("count", x + start)
        return x + start

    # counter(10) returns a generator directly (no intermediate wrapper)
    # The VM evaluates it as a DoExpr node
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)  # Returns generator directly
    return wrapper
```

#### How the VM Processes @do Output [R13-E]

1. User calls `counter(10)` → returns a generator directly
2. This is a DoExpr — accepted by `run()`, `WithHandler`, `Resume`, etc.
3. The VM evaluates the DoExpr via `EvalDoExpr`:
   - For generators: steps via `GenNext`/`GenSend`/`GenThrow` PythonCalls
   - For Pure(value): delivers value immediately
   - For Call(f, args, kwargs): evaluates f and args, then calls
4. `yield <effect>` → classified by driver as `Yielded::Effect`/`Yielded::DoCtrl`
5. `return <value>` → `StopIteration(value)` → `PyCallOutcome::GenReturn(value)`
6. Generator return produces the final `T` in `Program[T]`.

#### DoExpr Input Rule (Reiterated) [R14-A]

DoExpr is control IR. Effects are data resolved via explicit `Perform(effect)`.
Generators yield DoCtrl nodes (or source-level effect values lowered to Perform).

```
Pure(value)                      → evaluates to value immediately
Call(f: DoExpr, args: [DoExpr])  → evaluates f and args, calls f(*args, **kwargs)
Map(source: DoExpr, f)           → evaluates source, applies f
FlatMap(source: DoExpr, binder)  → evaluates source, applies binder, evaluates result
Perform(effect)                  → dispatches effect through handler stack
```

No `to_generator()` at VM level — that's a Python API detail for backward compatibility.

[SUPERSEDED BY R15-A / SPEC-KPC-001 — KPC is now a call-time macro, not a runtime effect.]
~~KPC (`PyKPC`) was an Effect dispatched to the KPC handler [SUPERSEDED]. The handler
extracts `execution_kernel` and resolved args, then emits
`DoCtrl::Call { f: kernel, args, kwargs }`. The VM calls the kernel,
gets a generator, and pushes it as a frame — same as any other `Call`.~~
[R15-A: `KleisliProgram.__call__()` now returns a `Call` DoCtrl directly via macro expansion. No KPC handler is involved.]

### Store and Env Lifecycle [R8-J]

End-to-end data flow from `run()` parameters to `RunResult`:

```
 User calls run(program, handlers=[state, reader], env={"a": 1}, store={"x": 0})
      │
      ▼
 ┌─────────────────────────────────────────────────┐
 │  Step 1: Initialize RustStore                    │
 │                                                  │
 │  vm.rust_store.state = {"x": Value(0)}          │
 │  vm.rust_store.env   = {"a": Value(1)}          │
 │  vm.rust_store.log   = []                        │
 └─────────────────────┬───────────────────────────┘
                       │
                       ▼
 ┌─────────────────────────────────────────────────┐
 │  Step 2: Wrap handlers + execute                 │
 │                                                  │
 │  WithHandler(state,                              │
 │    WithHandler(reader,                           │
 │      program))                                   │
 │                                                  │
 │  During execution:                               │
 │    yield Get("x")  → state handler reads         │
 │                       rust_store.state["x"]      │
 │    yield Put("x",1) → state handler writes       │
 │                       rust_store.state["x"] = 1  │
 │    yield Ask("a")  → reader handler reads        │
 │                       rust_store.env["a"]        │
 │    yield Tell("hi") → writer handler appends     │
 │                       rust_store.log.push("hi")  │
 └─────────────────────┬───────────────────────────┘
                       │
                       ▼
 ┌─────────────────────────────────────────────────┐
 │  Step 3: Extract RunResult                       │
 │                                                  │
 │  result.result    = Ok(return_value)             │
 │  result.raw_store = {"x": 1}   ← state only     │
 │                                                  │
 │  NOT in raw_store:                               │
 │    env (read-only, user already has it)          │
 │    log (accessible via writer handler if needed) │
 └─────────────────────────────────────────────────┘
```

**RustStore field mapping**:

| RustStore field | Initialized from | Modified by | Extracted into |
|-----------------|------------------|-------------|----------------|
| `state` | `run(store={...})` | `Put`, `Modify` effects | `RunResult.raw_store` |
| `env` | `run(env={...})` | Never (read-only, API-5) | Not extracted |
| `log` | Empty `[]` | `Tell` effect | Not extracted (handler-specific) |

**Error case**: If the program raises an exception, `RunResult` still contains
`raw_store` reflecting the store state at the point of failure. The `result`
field is `Err(exception)`.

### PyVM Internal Methods for Lifecycle [R8-J]

These methods support the `run()`/`async_run()` lifecycle but are **internal** —
users never call them directly (SPEC-009 §9).

```rust
#[pymethods]
impl PyVM {
    /// Initialize state entries from Python dict.
    /// Called by run() before execution.
    fn put_state(&mut self, key: String, value: PyObject) {
        self.vm.rust_store.put(key, Value::Python(value));
    }

    /// Initialize environment entries from Python dict.
    /// Called by run() before execution.
    fn put_env(&mut self, key: String, value: PyObject) {
        self.vm.rust_store.env.insert(key, Value::Python(value));
    }

    /// Extract final state as Python dict.
    /// Called by run() after execution to build RunResult.raw_store.
    fn state_items(&self, py: Python<'_>) -> PyResult<PyObject> {
        let dict = PyDict::new(py);
        for (k, v) in &self.vm.rust_store.state {
            dict.set_item(k, v.to_pyobject(py)?)?;
        }
        Ok(dict.into())
    }

    /// Extract environment items (for debugging; not in RunResult).
    fn env_items(&self, py: Python<'_>) -> PyResult<PyObject> {
        let dict = PyDict::new(py);
        for (k, v) in &self.vm.rust_store.env {
            dict.set_item(k, v.to_pyobject(py)?)?;
        }
        Ok(dict.into())
    }

    /// Extract logs (for debugging; not in RunResult).
    fn logs(&self, py: Python<'_>) -> PyResult<PyObject> {
        let list = PyList::new(py, &[])?;
        for v in &self.vm.rust_store.log {
            list.append(v.to_pyobject(py)?)?;
        }
        Ok(list.into())
    }
}
```

---

## Control Primitives

```rust
/// Control primitives that can be yielded by handlers.
/// [R13-B] Added Pure, Map, FlatMap. Updated Call to take DoExpr args.
#[derive(Debug, Clone)]
pub enum DoCtrl {
    /// Resume(k, v) - Call-resume (returns to handler after k completes)
    Resume {
        continuation: Continuation,
        value: Value,
    },
    
    /// Transfer(k, v) - Tail-transfer (non-returning, abandons handler)
    Transfer {
        continuation: Continuation,
        value: Value,
    },

    /// TransferThrow(k, exc) - Tail-transfer with exception (non-returning).
    /// Like Transfer but throws `exc` into continuation `k` via gen.throw().
    /// Used by scheduler to deliver errors to waiting continuations
    /// (e.g., TaskCancelledError, Gather fail-fast). See SPEC-SCHED-001.
    TransferThrow {
        continuation: Continuation,
        exception: PyException,
    },
    
    /// Delegate(effect) - Non-terminal re-perform to outer handler. [R15-A]
    /// Handler receives result back via K_new continuation swap.
    Delegate {
        effect: Effect,
    },
    
    /// Pass(effect) - Terminal pass-through to outer handler. [R15-A]
    /// Current handler frames are cleared; no value returns here.
    Pass {
        effect: Effect,
    },
    
    /// WithHandler(handler, expr) - Install handler and evaluate DoExpr under it
    WithHandler {
        handler: Handler,
        expr: Py<PyAny>,
    },

    /// PythonAsyncSyntaxEscape(action) - Request async context execution.
    /// Used by async_run to await Python coroutines.
    PythonAsyncSyntaxEscape {
        /// Callable returning an awaitable
        action: Py<PyAny>,
    },
    
    /// GetContinuation - Capture current continuation (callsite k_user)
    GetContinuation,
    
    /// GetHandlers - Get handlers from callsite scope (full chain, innermost first)
    GetHandlers,
    
    /// CreateContinuation(expr, handlers) - Create unstarted continuation
    CreateContinuation {
        /// DoExpr to evaluate (Pure, Call, Map, FlatMap, or Effect) [R13-B]
        expr: Py<PyAny>,
        /// Handlers in innermost-first order (as returned by GetHandlers)
        handlers: Vec<Handler>,
    },
    
    /// ResumeContinuation(k, v) - Resume captured or created continuation
    /// (v is ignored for unstarted continuations)
    ResumeContinuation {
        continuation: Continuation,
        value: Value,
    },

    /// [R13-B] Pure(value) - Literal value node. Evaluates to value immediately, zero cost.
    /// This is the VM's literal — not an effect, not a call. Just a value.
    Pure {
        value: Value,
    },

    /// [R13-C] Call(f, args, kwargs, metadata) - Function application with DoExpr args.
    ///
    /// Semantics: VM evaluates `f` (a DoExpr, typically Pure(callable)), then evaluates
    /// each arg DoExpr sequentially left-to-right, then calls `f(*resolved_args, **resolved_kwargs)`.
    /// The result is a generator pushed as a PythonGenerator frame.
    ///
    /// No distinction between "DoThunk path" and "kernel path" — both are Call with DoExpr args.
    /// [SUPERSEDED BY R15-A / SPEC-KPC-001] ~~KPC handler emits Call(f: Pure(kernel), args: [Pure(arg1), Pure(arg2), ...], kwargs, meta).~~
    /// [R15-A: `KleisliProgram.__call__()` emits `Call(Pure(kernel), args, kwargs, meta)` directly via macro expansion.]
    /// VM evaluates each Pure(arg) → arg, then calls kernel(*args, **kwargs).
    ///
    /// Metadata is extracted by the driver (with GIL) or constructed by RustHandlerPrograms.
    Call {
        /// The callable DoExpr (typically Pure(callable)) [R13-C]
        f: Py<PyAny>,
        /// Positional argument DoExprs (evaluated left-to-right) [R13-C]
        args: Vec<Py<PyAny>>,
        /// Keyword argument DoExprs [R13-C]
        kwargs: Vec<(String, Py<PyAny>)>,
        /// Call stack metadata (function name, source location)
        metadata: CallMetadata,
    },

    /// [R13-B] Map(source, f) - Functor map. Replaces DerivedProgram.
    /// Evaluates source DoExpr, applies f to the result, returns f(result).
    /// f is a Python callable (not a DoExpr — it's a pure function).
    Map {
        /// Source DoExpr to evaluate
        source: Py<PyAny>,
        /// Mapping function (Python callable)
        f: Py<PyAny>,
    },

    /// [R13-B] FlatMap(source, binder) - Monadic bind. Replaces DerivedProgram.
    /// Evaluates source DoExpr, applies binder to the result, evaluates binder(result) as a DoExpr.
    /// binder is a Python callable that returns a DoExpr.
    FlatMap {
        /// Source DoExpr to evaluate
        source: Py<PyAny>,
        /// Binder function (Python callable returning DoExpr)
        binder: Py<PyAny>,
    },

    /// Eval(expr, handlers) - Evaluate a DoExpr in a fresh scope. [R9-H] [R13-F]
    ///
    /// Atomically creates an unstarted continuation with the given handler
    /// chain and evaluates the DoExpr within it. The caller is suspended;
    /// when the evaluation completes, the VM resumes the caller with the
    /// result. Equivalent to CreateContinuation + ResumeContinuation but
    /// as a single atomic step.
    ///
    /// The DoExpr can be any DoExpr node: [R13-F]
    /// - Pure(value): evaluates to value immediately
    /// - Call(f, args, kwargs, meta): evaluates f and args, calls f(*args, **kwargs)
    /// - Map(source, f): evaluates source, applies f
    /// - FlatMap(source, binder): evaluates source, applies binder, evaluates result
    /// - Effect: dispatches through continuation's handler stack
    ///
    /// [SUPERSEDED BY R15-A / SPEC-KPC-001] ~~Primary use: KPC handler resolving args with the full callsite handler
    /// chain (captured via GetHandlers), avoiding busy boundary issues.~~
    /// [R15-A: KPC handler removed. Eval remains available for general DoExpr evaluation in scoped contexts.]
    Eval {
        /// The DoExpr to evaluate (Pure, Call, Map, FlatMap, or Effect) [R13-F]
        expr: Py<PyAny>,
        /// Handler chain for the continuation's scope (from GetHandlers)
        handlers: Vec<Handler>,
    },

    /// GetCallStack - Walk frames and return call stack metadata. [R9-B]
    ///
    /// Pure Rust frame walk — no GIL, no Python interaction.
    /// Returns Vec<CallMetadata> from PythonGenerator frames that have metadata.
    /// Walks current segment + caller chain (innermost frame first).
    /// Analogous to GetHandlers (structural VM inspection, not an effect).
    GetCallStack,
}
```

**Note**: There is no `Return` DoCtrl. Handler return is implicit:
when a handler program finishes, the VM applies `handle_handler_return(value)`
semantics (return to caller; root handler return abandons callsite).

**Async note**: `PythonAsyncSyntaxEscape` yields `PythonCall::CallAsync` via
`handle_do_ctrl`. It is only valid under `async_run` / `VM.run_async`.

---

## Primitive Handlers

These implementations show how DoCtrls modify VM state and return the next Mode.

### WithHandler (Creates Prompt + Body Structure)

```rust
impl VM {
    /// Install a handler and run a program under it.
    /// 
    /// Creates the following structure:
    /// 
    ///   outside_seg          <- current_segment (where result goes)
    ///        ^
    ///        |
    ///   prompt_seg           <- handler boundary (abandon returns here)
    ///        ^                  kind = PromptBoundary { handled_marker }
    ///        |
    ///   body_seg             <- body program runs here
    ///                           scope_chain = [handler_marker] ++ outside.scope_chain
    ///
    /// Returns: PythonCall to start body program (caller returns NeedsPython)
    fn handle_with_handler(&mut self, handler: Handler, expr: Py<PyAny>) -> PythonCall {
        let handler_marker = Marker::fresh();
        let outside_seg_id = self.current_segment;
        let outside_scope = self.segments[outside_seg_id.index()].scope_chain.clone();
        
        // 1. Create prompt segment (handler boundary)
        //    scope_chain = outside's scope (handler NOT in scope at prompt level)
        let prompt_seg = Segment::new_prompt(
            handler_marker,
            Some(outside_seg_id),  // returns to outside
            outside_scope.clone(),
            handler_marker,
        );
        let prompt_seg_id = self.alloc_segment(prompt_seg);
        
        // 2. Register handler WITH prompt_seg_id (no search needed later)
        self.handlers.insert(handler_marker, HandlerEntry {
            handler,
            prompt_seg_id,
        });
        
        // 3. Create body segment with handler in scope
        //    scope_chain = [handler_marker] ++ outside_scope (innermost first)
        let mut body_scope = vec![handler_marker];
        body_scope.extend(outside_scope);
        
        let body_seg = Segment::new(
            handler_marker,
            Some(prompt_seg_id),  // returns to PROMPT, not outside
            body_scope,
        );
        let body_seg_id = self.alloc_segment(body_seg);
        
        // 4. Switch to body segment
        self.current_segment = body_seg_id;
        
        // 5. Return PythonCall to evaluate body DoExpr [R13-E]
        PythonCall::EvalDoExpr { expr }
    }
}
```

### Dispatch (All Effects, Top-Only Busy Boundary)

```rust
impl VM {
    /// Start dispatching an effect to handlers. [R8-G]
    ///
    /// ALL effects go through this path. Two handler variants,
    /// one dispatch protocol.
    ///
    /// Returns Ok(StepEvent) if dispatch started successfully.
    /// Returns Err(VMError) if no handler found.
    /// Start dispatch for an effect. [R11-E]
    ///
    /// `effect` is opaque `Py<PyAny>` — the VM does not inspect it.
    /// `py` is required because `can_handle()` needs GIL for isinstance checks.
    fn start_dispatch(&mut self, py: Python<'_>, effect: Py<PyAny>) -> Result<StepEvent, VMError> {
        // Lazy pop completed dispatch contexts
        self.lazy_pop_completed();

        // Get current scope_chain
        let scope_chain = self.current_scope_chain();

        // Compute visible handlers (top-only busy exclusion)
        let handler_chain = self.visible_handlers(&scope_chain);

        if handler_chain.is_empty() {
            return Err(VMError::unhandled_effect_opaque());
        }

        // Find first handler that can handle this effect [R11-D]
        // can_handle() receives &Bound<'_, PyAny> — handler does isinstance
        let effect_bound = effect.bind(py);
        let (handler_idx, handler_marker, entry) =
            self.find_matching_handler(py, &handler_chain, effect_bound)?;

        let prompt_seg_id = entry.prompt_seg_id;
        let handler = entry.handler.clone();

        let dispatch_id = DispatchId::fresh();

        // Capture callsite continuation
        let current_seg = &self.segments[self.current_segment.index()];
        let k_user = Continuation::capture(current_seg, self.current_segment, Some(dispatch_id));

        // Push dispatch context [R11-E]
        self.dispatch_stack.push(DispatchContext {
            dispatch_id,
            effect: effect.clone_ref(py),  // Py<PyAny> — opaque
            handler_chain: handler_chain.clone(),
            handler_idx,
            k_user: k_user.clone(),
            prompt_seg_id,
            completed: false,
        });

        // Create handler execution segment
        let handler_seg = Segment::new(
            handler_marker,
            Some(prompt_seg_id),
            scope_chain,
        );
        let handler_seg_id = self.alloc_segment(handler_seg);
        self.current_segment = handler_seg_id;

        // Invoke handler — two variants, same dispatch chain
        Ok(self.invoke_handler(py, handler, effect_bound, k_user))
    }

    /// Invoke a handler and return the next StepEvent. [R8-G] [R11-D]
    fn invoke_handler(
        &mut self,
        py: Python<'_>,
        handler: Handler,
        effect: &Bound<'_, PyAny>,
        k_user: Continuation,
    ) -> StepEvent {
        match handler {
            Handler::RustProgram(rust_handler) => {
                // Rust program handler: create program instance and step it.
                // Handler receives opaque effect — downcasts internally.
                let program = rust_handler.create_program();
                let step = {
                    let mut guard = program.lock().expect("Rust program lock poisoned");
                    guard.start(py, effect, k_user.clone(), &mut self.rust_store)
                };
                self.apply_rust_program_step(step, program)
            }
            Handler::Python(py_handler) => {
                // Python handler: call with (effect, k_user) and expect a Program
                // Effect is passed as-is — it's already a Python object.
                self.pending_python = Some(PendingPython::CallPythonHandler {
                    k_user: k_user.clone(),
                    effect: effect.clone().unbind(),
                });
                StepEvent::NeedsPython(PythonCall::CallHandler {
                    handler: py_handler,
                    effect: effect.clone().unbind(),
                    continuation: k_user,
                })
            }
        }
    }

    /// Apply a RustProgramStep and return the next StepEvent.
    fn apply_rust_program_step(
        &mut self,
        step: RustProgramStep,
        program: RustProgramRef,
    ) -> StepEvent {
        match step {
            RustProgramStep::Yield(yielded) => {
                let segment = &mut self.segments[self.current_segment.index()];
                segment.push_frame(Frame::RustProgram { program });
                self.mode = Mode::HandleYield(yielded);
                StepEvent::Continue
            }
            RustProgramStep::Return(value) => {
                self.mode = Mode::Deliver(value);
                StepEvent::Continue
            }
            RustProgramStep::Throw(exc) => {
                self.mode = Mode::Throw(exc);
                StepEvent::Continue
            }
            RustProgramStep::NeedsPython(call) => {
                // [R8-H] Handler needs a Python callback (e.g., Modify).
                // Re-push handler frame so resume() returns to it.
                let segment = &mut self.segments[self.current_segment.index()];
                segment.push_frame(Frame::RustProgram { program });
                self.pending_python = Some(PendingPython::RustProgramContinuation {
                    marker: self.dispatch_stack.last()
                        .map(|d| d.handler_marker)
                        .unwrap_or(Marker::fresh()),
                    k: Continuation::empty(),
                });
                StepEvent::NeedsPython(call)
            }
        }
    }

    // [R8-H] apply_handler_action() DELETED.
    // Was: fn apply_handler_action(&mut self, action: HandlerAction) -> StepEvent
    // HandlerAction/NeedsPython/StdlibContinuation no longer exist.
    // All handler actions now flow through RustProgramStep::Yield(Yielded::*)
    // and apply_rust_program_step(). Python callbacks from Modify are yielded
    // as RustProgramStep::Yield(Yielded::PythonCall(...)) by the handler program.

    /// Handle handler return (explicit or implicit).
    /// 
    /// Returns to the handler's caller segment. If the caller is the current
    /// dispatch's prompt boundary (root handler), this abandons the callsite
    /// and marks the dispatch completed.
    fn handle_handler_return(&mut self, value: Value) -> StepEvent {
        let Some(top) = self.dispatch_stack.last_mut() else {
            self.mode = Mode::Throw(PyException::runtime_error(
                "Return outside of dispatch"
            ));
            return StepEvent::Continue;
        };
        
        if let Some(caller_id) = self.segments[self.current_segment.index()].caller {
            if caller_id == top.prompt_seg_id {
                top.completed = true;
                self.consumed_cont_ids.insert(top.k_user.cont_id);
            }
        }
        
        self.mode = Mode::Deliver(value);
        StepEvent::Continue
    }
    
    /// Find first handler in chain that can handle the effect.
    /// 
    /// Returns (index, marker, entry) - index is the position in handler_chain.
    /// This index is CRITICAL for busy boundary computation.
    fn find_matching_handler(
        &self, 
        handler_chain: &[Marker], 
        effect: &Effect
    ) -> Result<(usize, Marker, HandlerEntry), VMError> {
        for (idx, &marker) in handler_chain.iter().enumerate() {
            if let Some(entry) = self.handlers.get(&marker) {
                if entry.handler.can_handle(effect) {
                    return Ok((idx, marker, entry.clone()));
                }
            }
        }
        Err(VMError::UnhandledEffect(effect.clone()))
    }
    
    /// Compute visible handlers (TOP-ONLY busy exclusion).
    /// 
    /// Only the current (topmost non-completed) dispatch creates a busy boundary.
    /// Visibility is computed from the CURRENT scope_chain so handlers installed
    /// inside a handler remain visible unless they are busy.
    fn visible_handlers(&self, scope_chain: &[Marker]) -> Vec<Marker> {
        let Some(top) = self.dispatch_stack.last() else {
            return scope_chain.to_vec();
        };
        
        if top.completed {
            return scope_chain.to_vec();
        }
        
        // Busy = handlers at indices 0..=handler_idx in top dispatch
        // Visible = current scope_chain minus busy handlers (preserve order)
        let busy: HashSet<Marker> = top.handler_chain[..=top.handler_idx]
            .iter()
            .copied()
            .collect();
        scope_chain
            .iter()
            .copied()
            .filter(|marker| !busy.contains(marker))
            .collect()
    }
    
    /// [Q13] Lazy cleanup of completed dispatch contexts.
    ///
    /// Called at the start of handle_do_ctrl(), start_dispatch(), handle_resume(),
    /// and handle_transfer(). Drops completed dispatches from the top of the stack
    /// so that subsequent handler lookups only see active dispatches.
    /// This is an optimization over eagerly cleaning up at completion time.
    fn lazy_pop_completed(&mut self) {
        while let Some(top) = self.dispatch_stack.last() {
            if top.completed {
                self.dispatch_stack.pop();
            } else {
                break;
            }
        }
    }
    
    fn current_scope_chain(&self) -> Vec<Marker> {
        self.segments[self.current_segment.index()].scope_chain.clone()
    }
    
    // NOTE: find_prompt_seg_for_marker is REMOVED.
    // prompt_seg_id is now stored in HandlerEntry at WithHandler time.
    // No linear search needed - O(1) lookup via handlers.get(marker).
}
```

### Resume + Continuation Primitives

The following functions cover captured continuations (Resume/Transfer) and created
continuations (ResumeContinuation). They also define handler introspection primitives
(GetContinuation/GetHandlers).

```rust
impl VM {
    /// Resume a continuation with call-resume semantics.
    /// 
    /// The continuation's frames_snapshot is materialized into a new segment.
    /// The current segment becomes the caller (returns here after k completes).
    fn handle_resume(&mut self, k: Continuation, value: Value) -> Mode {
        if !k.started {
            return Mode::Throw(PyException::runtime_error(
                "Resume on unstarted continuation; use ResumeContinuation"
            ));
        }
        // One-shot check
        if self.consumed_cont_ids.contains(&k.cont_id) {
            return Mode::Throw(PyException::runtime_error(
                "Continuation already resumed"
            ));
        }
        self.consumed_cont_ids.insert(k.cont_id);
        
        // Lazy pop completed dispatches
        self.lazy_pop_completed();
        
        // Check dispatch completion
        // RULE: dispatch_id is only Some for callsite continuations
        if let Some(dispatch_id) = k.dispatch_id {
            if let Some(top) = self.dispatch_stack.last_mut() {
                if top.dispatch_id == dispatch_id && top.k_user.cont_id == k.cont_id {
                    top.completed = true;
                }
            }
        }
        
        // Materialize continuation into new execution segment
        // (shallow clone of frames, Frame is small)
        let exec_seg = Segment {
            marker: k.marker,
            frames: (*k.frames_snapshot).clone(),
            caller: Some(self.current_segment),  // call-resume: returns here
            scope_chain: (*k.scope_chain).clone(),
            kind: SegmentKind::Normal,
        };
        let exec_seg_id = self.alloc_segment(exec_seg);
        
        // Switch to execution segment
        self.current_segment = exec_seg_id;
        
        Mode::Deliver(value)
    }
    
    /// Transfer to a continuation (tail-transfer, non-returning).
    /// 
    /// Does NOT set up return link. Current handler is abandoned.
    /// Marks dispatch completed when the target is k_user.
    fn handle_transfer(&mut self, k: Continuation, value: Value) -> Mode {
        if !k.started {
            return Mode::Throw(PyException::runtime_error(
                "Transfer on unstarted continuation; use ResumeContinuation"
            ));
        }
        // One-shot check
        if self.consumed_cont_ids.contains(&k.cont_id) {
            return Mode::Throw(PyException::runtime_error(
                "Continuation already resumed"
            ));
        }
        self.consumed_cont_ids.insert(k.cont_id);
        
        // Lazy pop completed dispatches
        self.lazy_pop_completed();
        
        // Check dispatch completion (Transfer completes when resuming callsite)
        if let Some(dispatch_id) = k.dispatch_id {
            if let Some(top) = self.dispatch_stack.last_mut() {
                if top.dispatch_id == dispatch_id && top.k_user.cont_id == k.cont_id {
                    top.completed = true;
                }
            }
        }
        
        // Materialize continuation
        let exec_seg = Segment {
            marker: k.marker,
            frames: (*k.frames_snapshot).clone(),
            caller: None,  // tail-transfer: no return
            scope_chain: (*k.scope_chain).clone(),
            kind: SegmentKind::Normal,
        };
        let exec_seg_id = self.alloc_segment(exec_seg);
        
        // Switch to execution segment
        self.current_segment = exec_seg_id;
        
        Mode::Deliver(value)
    }

    /// Resume a captured or created continuation.
    /// 
    /// Captured: same as Resume (call-resume semantics).
    /// Created: installs handlers, starts program, returns to current segment.
    fn handle_resume_continuation(&mut self, k: Continuation, value: Value) -> StepEvent {
        if k.started {
            self.mode = self.handle_resume(k, value);
            return StepEvent::Continue;
        }
        
        // Unstarted continuation: value is ignored, program starts fresh.
        if self.consumed_cont_ids.contains(&k.cont_id) {
            self.mode = Mode::Throw(PyException::runtime_error(
                "Continuation already resumed"
            ));
            return StepEvent::Continue;
        }
        self.consumed_cont_ids.insert(k.cont_id);
        
        let Some(program) = k.program.clone() else {
            self.mode = Mode::Throw(PyException::runtime_error(
                "Unstarted continuation missing program"
            ));
            return StepEvent::Continue;
        };
        
        // Install handlers (outermost first, so innermost ends up closest to program)
        let mut outside_seg_id = self.current_segment;
        let mut outside_scope = self.segments[outside_seg_id.index()].scope_chain.clone();
        
        for handler in k.handlers.iter().rev() {
            let handler_marker = Marker::fresh();
            let prompt_seg = Segment::new_prompt(
                handler_marker,
                Some(outside_seg_id),
                outside_scope.clone(),
                handler_marker,
            );
            let prompt_seg_id = self.alloc_segment(prompt_seg);
            
            self.handlers.insert(handler_marker, HandlerEntry {
                handler: handler.clone(),
                prompt_seg_id,
            });
            
            let mut body_scope = vec![handler_marker];
            body_scope.extend(outside_scope);
            
            let body_seg = Segment::new(
                handler_marker,
                Some(prompt_seg_id),
                body_scope,
            );
            let body_seg_id = self.alloc_segment(body_seg);
            
            outside_seg_id = body_seg_id;
            outside_scope = self.segments[body_seg_id.index()].scope_chain.clone();
        }
        
        self.current_segment = outside_seg_id;
        // WithHandler body has no call metadata (it's a handler scope, not a @do call)
        self.pending_python = Some(PendingPython::EvalDoExprFrame {  // [R13-E]
            metadata: None,
        });
        StepEvent::NeedsPython(PythonCall::EvalDoExpr { expr: program })  // [R13-E]
    }

    /// Handle a DoCtrl, returning the next StepEvent.
    fn handle_do_ctrl(&mut self, prim: DoCtrl) -> StepEvent {
        // Drop completed dispatches before inspecting handler context.
        self.lazy_pop_completed();
        match prim {
            DoCtrl::Resume { continuation, value } => {
                self.mode = self.handle_resume(continuation, value);
                StepEvent::Continue
            }
            DoCtrl::Transfer { continuation, value } => {
                self.mode = self.handle_transfer(continuation, value);
                StepEvent::Continue
            }
            DoCtrl::TransferThrow { continuation, exception } => {
                // Like handle_transfer but enters Mode::Throw instead of Mode::Value.
                // Materializes continuation frames then throws exception via gen.throw().
                self.mode = self.handle_transfer_throw(continuation, exception);
                StepEvent::Continue
            }
            DoCtrl::Delegate { effect } => {
                // Non-terminal re-perform: K_new swap, handler receives result [R15-B]
                self.handle_delegate(effect)
            }
            DoCtrl::Pass { effect } => {
                // Terminal pass-through: handler done, advance to outer [R15-A]
                self.handle_pass(effect)
            }
            DoCtrl::WithHandler { handler, expr } => {
                // WithHandler needs PythonCall to evaluate body DoExpr (no call metadata) [R13-E]
                let call = self.handle_with_handler(handler, expr);
                self.pending_python = Some(PendingPython::EvalDoExprFrame {  // [R13-E]
                    metadata: None,
                });
                StepEvent::NeedsPython(call)
            }
            DoCtrl::PythonAsyncSyntaxEscape { action } => {
                // Async-only escape to event loop
                self.pending_python = Some(PendingPython::AsyncEscape);
                StepEvent::NeedsPython(PythonCall::CallAsync {
                    func: action,
                    args: vec![],
                })
            }
            DoCtrl::GetContinuation => {
                let Some(top) = self.dispatch_stack.last() else {
                    self.mode = Mode::Throw(PyException::runtime_error(
                        "GetContinuation called outside handler context"
                    ));
                    return StepEvent::Continue;
                };
                self.mode = Mode::Deliver(Value::Continuation(top.k_user.clone()));
                StepEvent::Continue
            }
            DoCtrl::GetHandlers => {
                let Some(top) = self.dispatch_stack.last() else {
                    self.mode = Mode::Throw(PyException::runtime_error(
                        "GetHandlers called outside handler context"
                    ));
                    return StepEvent::Continue;
                };
                // Return full handler_chain from callsite scope (innermost first)
                let mut handlers = Vec::new();
                for marker in top.handler_chain.iter() {
                    let Some(entry) = self.handlers.get(marker) else {
                        self.mode = Mode::Throw(PyException::runtime_error(
                            "GetHandlers: missing handler entry"
                        ));
                        return StepEvent::Continue;
                    };
                    handlers.push(entry.handler.clone());
                }
                self.mode = Mode::Deliver(Value::Handlers(handlers));
                StepEvent::Continue
            }
            DoCtrl::CreateContinuation { expr, handlers } => {
                let cont = Continuation::create_unstarted(expr, handlers);
                self.mode = Mode::Deliver(Value::Continuation(cont));
                StepEvent::Continue
            }
            DoCtrl::ResumeContinuation { continuation, value } => {
                self.handle_resume_continuation(continuation, value)
            }
            DoCtrl::Call { f, args, kwargs, metadata } => {
                // [R13-C] Full DoExpr evaluation for Call.
                // Evaluate f, then each arg in args, left-to-right, then invoke.
                //
                // Fast path: if f is Pure(callable) AND all args are Pure(value),
                // skip the multi-step dance — extract values and invoke directly.
                if let Some(fast) = self.try_call_fast_path(&f, &args, &kwargs, &metadata) {
                    return fast;
                }
                // Slow path: evaluate f as DoExpr first.
                // After f resolves, evaluate args[0], args[1], ... sequentially.
                self.pending_python = Some(PendingPython::CallEvalProgress {
                    phase: CallEvalPhase::EvalF,
                    resolved_f: None,
                    remaining_args: args,
                    resolved_args: Vec::new(),
                    kwargs,
                    metadata,
                });
                self.eval_do_expr(f)  // → NeedsPython or Continue (if Pure/DoCtrl)
            }
            DoCtrl::Eval { expr, handlers } => {
                // [R9-H] Evaluate a DoExpr in a fresh scope with given handlers.
                // Atomically equivalent to CreateContinuation + ResumeContinuation.
                let cont = Continuation::create_unstarted(expr, handlers);
                self.handle_resume_continuation(cont, Value::None)
            }
            DoCtrl::GetCallStack => {
                // [R9-B] Walk frames across segments, collect CallMetadata.
                let mut stack = Vec::new();
                let mut seg_id = self.current_segment;
                while let Some(id) = seg_id {
                    let seg = &self.segments[id.index()];
                    for frame in seg.frames.iter().rev() {
                        if let Frame::PythonGenerator { metadata: Some(m), .. } = frame {
                            stack.push(m.clone());
                        }
                    }
                    seg_id = seg.caller;
                }
                self.mode = Mode::Deliver(Value::CallStack(stack));
                StepEvent::Continue
            }
            DoCtrl::Pure { value } => {
                // [R13-B] Pure(value) — literal value node. Evaluates to value immediately.
                self.mode = Mode::Deliver(value);
                StepEvent::Continue
            }
            DoCtrl::Map { source, f } => {
                // [R13-B] Map(source, f) — functor map. Evaluate source DoExpr, apply f.
                // If source is Pure(v), fast-path: call f(v) directly.
                // Otherwise, eval_do_expr handles the source; MapPending routes the result.
                self.pending_python = Some(PendingPython::MapPending { f });
                self.eval_do_expr(source)
            }
            DoCtrl::FlatMap { source, binder } => {
                // [R13-B] FlatMap(source, binder) — monadic bind.
                // Evaluate source DoExpr, call binder(result), evaluate binder's return DoExpr.
                self.pending_python = Some(PendingPython::FlatMapPending { binder });
                self.eval_do_expr(source)
            }
            _ => {
                self.mode = Mode::Throw(PyException::not_implemented(
                    format!("Primitive not yet implemented: {:?}", prim)
                ));
                StepEvent::Continue
            }
        }
    }
    
    /// [R13-C] [R13-I] Evaluate a DoExpr node. GIL-free. Returns StepEvent.
    /// Reads the tag to determine the DoExpr variant without GIL.
    /// Pure(value): delivers immediately — zero Python round-trips.
    /// Other DoCtrl: extracts fields GIL-free, processes recursively.
    /// Effect / Unknown: emits NeedsPython for driver to handle.
    fn eval_do_expr(&mut self, expr: Py<PyAny>) -> StepEvent {
        let tag = unsafe { read_do_expr_tag(&expr) };
        match tag {
            DoExprTag::Pure => {
                // [R13-I] GIL-free: read .value from frozen PyPure struct
                let value = unsafe { read_field::<PyPure>(&expr, |p| p.value.clone()) };
                // Deliver synchronously — the current pending_python state
                // (MapPending, CallEvalProgress, etc.) receives this as a Value.
                self.receive_python_result_inline(
                    PyCallOutcome::Value(Value::Python(value))
                )
            }
            _ if tag.is_do_ctrl() => {
                // Non-Pure DoCtrl (Call, Map, etc.) — extract and process.
                // This re-enters handle_do_ctrl, which may emit NeedsPython
                // for the parts that require Python (e.g., calling a function).
                let do_ctrl = extract_do_ctrl(tag, &expr);
                self.handle_do_ctrl(do_ctrl)
            }
            _ => {
                // Effect or Unknown — need Python driver.
                StepEvent::NeedsPython(PythonCall::EvalDoExpr { expr })
            }
        }
    }

    /// [R13-C] [R13-I] Fast path for Call when f and all args are Pure(value).
    /// GIL-free: reads tags and extracts .value fields from frozen pyclasses.
    /// Returns None if any DoExpr is not Pure (caller falls through to slow path).
    fn try_call_fast_path(
        &mut self,
        f: &Py<PyAny>,
        args: &[Py<PyAny>],
        kwargs: &Option<Py<PyAny>>,
        metadata: &CallMetadata,
    ) -> Option<StepEvent> {
        // Check f
        if unsafe { read_do_expr_tag(f) } != DoExprTag::Pure { return None; }
        let callable = unsafe { read_field::<PyPure>(f, |p| p.value.clone()) };
        // Check all args
        let mut resolved = Vec::with_capacity(args.len());
        for arg in args {
            if unsafe { read_do_expr_tag(arg) } != DoExprTag::Pure { return None; }
            resolved.push(unsafe { read_field::<PyPure>(arg, |p| p.value.clone()) });
        }
        // All Pure — invoke callable(*resolved_args, **kwargs) via Python.
        // This is the ONE NeedsPython for the entire Call — just the invocation.
        self.pending_python = Some(PendingPython::EvalDoExprFrame {
            metadata: Some(metadata.clone()),
        });
        Some(StepEvent::NeedsPython(PythonCall::CallFunc {
            func: callable,
            args: resolved.into_iter().map(|v| Value::Python(v)).collect(),
        }))
    }

    /// Handle Delegate (non-terminal re-perform): K_new swap. [R15-B]
    /// 
    /// Unlike Pass (terminal), Delegate captures K_new from the handler's state
    /// so the outer handler's Resume sends the value BACK to the delegating handler.
    /// See SPEC-VM-010 for the full K_new continuation swap mechanism.
    /// 
    /// INVARIANT: Delegate can only be called from a handler execution context.
    fn handle_delegate(&mut self, effect: Effect) -> StepEvent {
        let top = self.dispatch_stack.last_mut()
            .expect("Delegate called outside of dispatch context");
        
        let inner_seg_id = self.current_segment;
        
        // [R15-B] Capture K_new from delegating handler's state BEFORE clearing frames.
        // K_new is a continuation that, when resumed, sends the value back to this handler.
        let k_new = self.capture_continuation(inner_seg_id);
        
        // Clear the delegating handler's frames (handler is suspended, not terminated).
        if let Some(seg) = self.segments.get_mut(inner_seg_id) {
            seg.frames.clear();
        }
        
        // [R15-B] Swap k_user → K_new so outer handler resumes the delegating handler.
        let original_k_user = top.k_user.clone();
        top.k_user = k_new;  // Outer handler sees K_new, not original k_user
        
        // Advance handler_idx to find next handler
        let handler_chain = &top.handler_chain;
        let start_idx = top.handler_idx + 1;
        
        for idx in start_idx..handler_chain.len() {
            let marker = handler_chain[idx];
            if let Some(entry) = self.handlers.get(&marker) {
                if entry.handler.can_handle(&effect) {
                    top.handler_idx = idx;
                    top.effect = effect.clone();
                    
                    let handler = entry.handler.clone();
                    let scope_chain = self.current_scope_chain();
                    let handler_seg = Segment::new(
                        marker,
                        Some(inner_seg_id),
                        scope_chain,
                    );
                    let handler_seg_id = self.alloc_segment(handler_seg);
                    self.current_segment = handler_seg_id;
                    
                    // Outer handler receives K_new (already swapped above)
                    return self.invoke_handler(handler, &effect, top.k_user.clone());
                }
            }
        }
        
        self.mode = Mode::Throw(PyException::runtime_error(
            format!("Delegate: no outer handler for effect {:?}", effect)
        ));
        StepEvent::Continue
    }
    
    /// Handle Pass (terminal pass-through): old Delegate semantics. [R15-A]
    /// 
    /// Handler gives up control entirely. k_user passes unchanged to outer handler.
    /// Identical to pre-R15 handle_delegate.
    fn handle_pass(&mut self, effect: Effect) -> StepEvent {
        let top = self.dispatch_stack.last_mut()
            .expect("Pass called outside of dispatch context");
        
        let inner_seg_id = self.current_segment;
        
        // Clear frames — Pass is terminal, handler is done.
        if let Some(seg) = self.segments.get_mut(inner_seg_id) {
            seg.frames.clear();
        }
        
        let handler_chain = &top.handler_chain;
        let start_idx = top.handler_idx + 1;
        
        for idx in start_idx..handler_chain.len() {
            let marker = handler_chain[idx];
            if let Some(entry) = self.handlers.get(&marker) {
                if entry.handler.can_handle(&effect) {
                    top.handler_idx = idx;
                    top.effect = effect.clone();
                    
                    let handler = entry.handler.clone();
                    let scope_chain = self.current_scope_chain();
                    let handler_seg = Segment::new(
                        marker,
                        Some(inner_seg_id),
                        scope_chain,
                    );
                    let handler_seg_id = self.alloc_segment(handler_seg);
                    self.current_segment = handler_seg_id;
                    
                    // k_user unchanged — outer handler resumes original callsite
                    return self.invoke_handler(handler, &effect, top.k_user.clone());
                }
            }
        }
        
        self.mode = Mode::Throw(PyException::runtime_error(
            format!("Pass: no outer handler for effect {:?}", effect)
        ));
        StepEvent::Continue
    }
}
```

---

## Memory Management

### Segment Pool

```rust
impl VM {
    fn alloc_segment(&mut self, segment: Segment) -> SegmentId {
        if let Some(id) = self.free_segments.pop() {
            self.segments[id.0 as usize] = segment;
            id
        } else {
            let id = SegmentId(self.segments.len() as u32);
            self.segments.push(segment);
            id
        }
    }
    
    fn free_segment(&mut self, id: SegmentId) {
        self.segments[id.0 as usize] = Segment::new(
            Marker(0), None, Vec::new()
        );
        self.free_segments.push(id);
    }
}
```

### Callback Lifecycle

```rust
// Callbacks are stored in VM callback table (HashMap<CallbackId, Callback>).
// 
// 1. Register: vm.register_callback(Box::new(|v, vm| ...)) -> CallbackId
// 2. Frame holds: Frame::RustReturn { cb: CallbackId }
// 3. Execute: vm.consume_callback(cb) removes and returns the callback
// 4. Callback is consumed (FnOnce) and dropped after execution
//
// This allows Frames to be Clone (CallbackId is Copy) while
// still supporting FnOnce semantics for callbacks.
```

### PyObject Lifecycle

```rust
// PyObjects in Value::Python are Py<PyAny> which are GIL-independent.
// They are reference-counted by Python's GC.
// 
// When a Value::Python is dropped, the Py<PyAny> decrements the refcount.
// This happens automatically via Drop.
//
// IMPORTANT: Dropping Py<PyAny> without GIL is safe but may defer
// the actual Python object destruction until next GIL acquisition.
```

---

## Invariants

### INV-1: GIL Boundaries

```
GIL is ONLY held during:
  - PythonCall execution
  - Value::to_pyobject / from_pyobject (driver only)
  - Effect::to_pyobject / Continuation::to_pyobject (driver only)
  - Final result extraction

GIL is RELEASED during:
  - vm.step() execution
  - RustProgram handler execution (standard handlers)
  - Segment/frame management
```

### INV-2: Segment Ownership

```
All segments are owned by VM.segments arena.
Continuations hold snapshots (Arc<Vec<Frame>>), not segment references.
Resume materializes snapshot into fresh segment.
Segment can only be mutated via VM methods.

Arena slot reuse via free-list is allowed but not required by this spec.
```

### INV-3: One-Shot Continuations

```
ContId is checked in consumed_cont_ids before resume.
Double-resume returns Error, not panic.

Within a single dispatch, the callsite continuation (k_user) must be consumed
exactly once (Resume/Transfer/TransferThrow/Return). Any attempt to resume again (including
after yielding terminal Delegate) is a runtime error.

Multi-shot continuations are not supported. All continuations are one-shot only.
```

### INV-4: Scope Chain in Segment

```
Each Segment carries its own scope_chain.
Switching segments automatically restores scope.
No separate "current scope_chain" in VM state.
```

### INV-5: WithHandler Structure

```
WithHandler(h, body) at current_segment creates:

  prompt_seg:
    marker = handler_marker
    kind = PromptBoundary { handled_marker: handler_marker }
    caller = current_segment (outside)
    scope_chain = outside.scope_chain  // handler NOT in scope

  body_seg:
    marker = handler_marker
    kind = Normal
    caller = prompt_seg_id
    scope_chain = [handler_marker] ++ outside.scope_chain  // handler IN scope
```

### INV-6: Handler Execution Structure

```
start_dispatch creates:

  handler_exec_seg:
    marker = handler_marker
    kind = Normal
    caller = prompt_seg_id  // root handler return goes to prompt, not callsite
    scope_chain = callsite.scope_chain  // same scope as effect callsite
```

### INV-7: Dispatch ID Assignment

```
dispatch_id is Some IFF continuation is callsite (k_user).
All other continuations (handler-local) have dispatch_id = None.

Completion check requires BOTH:
  k.dispatch_id == Some(top.dispatch_id) AND
  k.cont_id == top.k_user.cont_id

Resume, Transfer, and Return all mark completion when they resolve k_user.
```

### INV-8: Busy Boundary (Top-Only)

```
Only the topmost non-completed dispatch creates a busy boundary.
Busy handlers = top.handler_chain[0..=top.handler_idx]
Visible handlers = current scope_chain minus busy handlers (preserve order)

This is MORE PERMISSIVE than union-all. Nested dispatches can see
handlers that are busy in outer dispatches, which matches algebraic
effect semantics (handlers are in scope based on their installation
point, not based on what's currently executing). Handlers installed
inside a handler remain visible unless they are busy.
```

### INV-9: All Effects Go Through Dispatch

```
ALL effects (including standard Get, Put, Modify, Ask, Tell) go through
the dispatch stack. There is NO bypass for any effect type. [R8-B]

Standard handlers are Rust-implemented (RustProgram) for performance but still:
  - Are installed via WithHandler or run(handlers=[...]) (explicit)
  - Go through dispatch (found via handler_chain lookup)
  - Can be intercepted, overridden, or replaced by users

To intercept state operations, install a custom handler that handles
Get/Put effects before the standard state handler in the scope chain.
```

### INV-10: Frame Stack Order

```
Frame stack top = LAST element of Vec (index frames.len()-1).
push_frame = frames.push() [O(1)]
pop_frame = frames.pop() [O(1)]

This avoids O(n) shifts from remove(0).
```

### INV-11: Segment Frames Are the Only Mutable Continuation State

```
Segment.frames is the ONLY mutable state during execution.

- Segment.frames: mutable Vec<Frame>, push/pop during execution
- Continuation.frames_snapshot: immutable Arc<Vec<Frame>>, frozen at capture

When a Continuation is captured:
  frames_snapshot = Arc::new(segment.frames.clone())

When a Continuation is resumed:
  new_segment.frames = (*k.frames_snapshot).clone()

This allows multiple Continuations to share frames via Arc while
each execution gets its own mutable working copy.
```

### INV-12: Continuation Kinds

```
Continuation has two kinds:

  started=true  (captured):
    - frames_snapshot/scope_chain/marker/dispatch_id are valid
    - program=None, handlers=[]

  started=false (created):
    - program/handlers are valid
    - frames_snapshot empty, scope_chain empty, dispatch_id=None
```

### INV-13: Step Event Classification

```
step() returns exactly one of:
  - Continue: internal transition, no Python needed, keep stepping
  - NeedsPython(call): must execute Python call, then receive_python_result()
  - Done(value): computation completed successfully
  - Error(e): computation failed

The driver loop spins on Continue (in allow_threads), only acquiring
GIL when NeedsPython is returned.
```

### INV-14: Mode Transitions

```
Mode transitions are deterministic:

  Deliver(v) + frames.pop() →
    - RustReturn: callback returns new Mode
    - RustProgram: resume → Yield/Return/Throw
    - PythonGenerator: NeedsPython(GenSend/GenNext)
    - empty frames: Return(v)

  Throw(e) + frames.pop() →
    - RustReturn: propagate (callbacks don't catch)
    - RustProgram: throw → Yield/Return/Throw
    - PythonGenerator: NeedsPython(GenThrow)
    - empty frames + caller: propagate up
    - empty frames + no caller: Error

  HandleYield(y) →  [R13-D: binary classification]
    - DoCtrl: handle_do_ctrl returns StepEvent (Continue or NeedsPython)
    - Effect: start_dispatch returns StepEvent (Continue or NeedsPython)
    - Unknown: Throw(TypeError)

  Return(v) →
    - caller exists: switch to caller, Deliver(v)
    - no caller: Done(v)
```

### INV-15: Generator Protocol

```
Python generators have three outcomes:

  yield value → PyCallOutcome::GenYield(Yielded)
    → Driver classifies (with GIL): DoCtrl/Effect/Unknown  [R13-D: binary]
    → VM receives pre-classified Yielded
    → Mode::HandleYield(yielded)

  return value (StopIteration) → PyCallOutcome::GenReturn(value)
    → frame consumed, value flows to caller
    → Mode::Deliver(value)

  raise exception → PyCallOutcome::GenError(exc)
    → Mode::Throw(exc)

EvalDoExpr/CallFunc/CallAsync/CallHandler return PyCallOutcome::Value(value) - NOT a generator step.  [R13-E]
CallHandler returns Value::Python(generator) after evaluating the DoExpr.
VM pushes Frame::PythonGenerator with started=false and metadata (from pending) when value is Value::Python(generator).

Generator start uses GenNext (__next__).
Generator resume uses GenSend (send).
Exception injection uses GenThrow (throw).

Rust program handlers mirror this protocol in Rust:
  - Yield → Mode::HandleYield(yielded)
  - Return → Mode::Deliver(value)
  - Throw → Mode::Throw(exc)
```

---

## Legacy Specs (Deprecated) — Differences

Legacy specs SPEC-006 and SPEC-007 are deprecated. This spec (008) is authoritative.
Key differences and decisions in 008:

- Busy boundary is **top-only**: only the topmost non-completed dispatch excludes
  busy handlers; nested dispatch does not consider older frames.
- `Delegate` and `Pass` are the two forwarding primitives; yielding a raw effect starts a new
  dispatch (does not forward). [R15-A]
- `yield Pass(effect)` is terminal pass-through; it does not return to the handler.
- `yield Delegate(effect)` is non-terminal re-perform; the handler receives the result back. [R15-B]
- Handler return is implicit; there is no `Return` DoCtrl.
- Program input is **ProgramBase only** (KleisliProgramCall or EffectBase); raw
  generators are rejected except via `start_with_generator()`.
- Continuations are one-shot only; multi-shot is not supported.
- Rust program handlers (RustProgramHandler/RustHandlerProgram) are first-class.
- `Call(f, args, kwargs, metadata)` is a DoCtrl for function invocation with
  call stack metadata (R9-A). `Eval(expr, handlers)` evaluates a DoExpr in a fresh scope
  (R9-H). `GetCallStack` walks frames (R9-B). [R13-A] DoThunk eliminated.
  [R14-A] DoExpr is control IR and effect values dispatch via explicit
  `Perform(effect)`. `Pure(value)` replaces PureProgram; `Map`/`FlatMap` replace
  DerivedProgram.

---

## Crate Structure

```
doeff-vm/
├── Cargo.toml
├── pyproject.toml
├── src/
│   ├── lib.rs           # Module root, PyO3 bindings
│   ├── vm.rs            # VM core + step handlers
│   ├── pyvm.rs          # Python-facing VM wrapper and entrypoints
│   ├── scheduler.rs     # Built-in scheduler handler
│   ├── handler.rs       # Standard/KPC handler implementations
│   ├── step.rs          # PyException and step-related helpers
│   ├── segment.rs       # Segment, SegmentKind
│   ├── frame.rs         # Frame enum, call metadata
│   ├── continuation.rs  # Continuation with Arc snapshot
│   ├── dispatch.rs      # DispatchContext, visibility logic
│   ├── do_ctrl.rs       # DoCtrl enum
│   ├── yielded.rs       # Yielded enum and routing types
│   ├── rust_store.rs    # RustStore (state/env/log)
│   ├── value.rs         # Value enum (Rust/Python interop)
│   ├── python_call.rs   # PythonCall, PendingPython, PyCallOutcome
│   ├── effect.rs        # Rust pyclass effects and KPC types
│   ├── ids.rs           # Marker/cont/segment IDs
│   ├── arena.rs         # Segment arena
│   ├── py_shared.rs     # GIL-free shared Python references
│   └── error.rs         # VMError enum
└── tests/
    └── ...
```

Implementation tasks and migration phases are tracked in
`ISSUE-rust-vm-implementation.md`, not in this spec.

---

## References

- PyO3 Guide: https://pyo3.rs/
- Rust Book: Ownership and Lifetimes
- "Retrofitting Effect Handlers onto OCaml" (PLDI 2021) - segment-based continuation design
- slotmap crate: https://docs.rs/slotmap/
- maturin: https://www.maturin.rs/
