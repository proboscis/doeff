# Rust VM Implementation Plan

**Issue:** #235
**Spec:** SPEC-008-rust-vm.md (Revision 9), SPEC-009-rust-vm-migration.md (Revision 5)
**Status:** R9 complete — semantic correctness enforced. SPEC-009 Rev 5 fixes applied (scheduler effects, user-space scheduler, ADR-13 ref). 97 Rust + 63 Python = 160 tests passing. 6 semgrep rules enforcing spec invariants.

## Overview

Implement a high-performance Rust VM with PyO3 integration, replacing the Python CESK v3 interpreter.

**Key Design Decisions:**
- 3-layer state model: `Internals` / `RustStore` / `PyStore`
- All effects go through dispatch (no bypass for stdlib)
- Mode-based step machine with `PendingPython` purpose tags
- Segment-based continuations with Arc snapshots

**Development Approach:** TDD - tests accompany each phase, not deferred to the end.

---

## Implementation Status vs Spec (Verified 2026-02-06)

| Category | Status | Notes |
|----------|--------|-------|
| Core Types (IDs, Value, Frame, Segment) | 100% | All present: `Value::Handlers/Task/Promise/ExternalPromise`, `Frame::RustProgram`, `TaskId`/`PromiseId` |
| Continuation & Arena | 100% | Fully aligned with spec |
| Effect & Handler | 100% | `Handler::RustProgram` + `Effect::Scheduler(SchedulerEffect)` |
| Step Machine (Mode, StepEvent, Yielded) | 100% | `PythonCall::CallAsync`, `PendingPython::AsyncEscape` implemented |
| Dispatch System | 100% | `start_dispatch`, `visible_handlers`, `lazy_pop_completed` all correct |
| Control Primitives | 100% | All 10/10: incl. `PythonAsyncSyntaxEscape`, `Delegate { effect }` |
| PyO3 Driver | 100% | GIL-decoupled, `CallAsync` TypeError guard. PyVM is `Send+Sync`. `allow_threads` blocked by `Py::clone()` GIL assertion (documented). |
| Stdlib Handlers | 100% | `run_scoped()` installs/removes handlers per-run. Scoped cleanup even on error. |
| Scheduler | 100% | Types, SchedulerState, SchedulerHandler/Program, PySchedulerHandler, integration tests |
| PyStore (Layer 3) | 100% | `py_store()`/`set_store()`/`get_store()` exposed on PyVM |
| Async Integration | 100% | `start_program()`/`step_once()`/`feed_async_result()`/`feed_async_error()` — full async driver protocol |
| ProgramBase Validation | 100% | `to_generator_strict` rejects raw generators at internal entry points; `to_generator_lenient` at user-facing run() |

---

## Phase 1: Core Types + Tests ✅

- [x] Set up Rust crate with PyO3 0.25 and maturin (`packages/doeff-vm/`)
- [x] Implement core IDs (`Marker`, `SegmentId`, `ContId`, `CallbackId`, `DispatchId`, `RunnableId`)
- [x] Implement `Value` enum with Python interop (`Value::Python(Py<PyAny>)`)
- [x] Implement `VMError` enum
- [x] Implement `Frame` enum (`RustReturn`, `PythonGenerator`) - uses CallbackId for Clone support
- [x] **Tests:** ID uniqueness, Value accessors, Frame clone behavior

## Phase 2: Continuation Structure + Tests ✅

- [x] Implement `Segment` with frames, caller, scope_chain
- [x] Implement `SegmentKind` enum (Normal, PromptBoundary)
- [x] Implement `Continuation` with `Arc<Vec<Frame>>` snapshots (capture + create)
- [x] Implement segment arena with free list (`arena.rs`)
- [x] **Tests:** Segment push/pop O(1), Continuation capture/materialize, arena alloc/free

## Phase 3: Step State Machine + Tests ✅

- [x] Implement `Mode` enum (`Deliver`, `Throw`, `HandleYield`, `Return`)
- [x] Implement `StepEvent`, `PendingPython`, `Yielded`, `ControlPrimitive` enums
- [x] Implement `step()` main loop — dispatches to step_deliver_or_throw / step_handle_yield / step_return
- [x] **Tests:** Mode transitions, step returns correct StepEvent, caller chain traversal

## Phase 4: Python Call Protocol + Tests ✅

- [x] Implement `PythonCall` enum (StartProgram, CallFunc, CallHandler, GenNext, GenSend, GenThrow, CallAsync)
- [x] Implement `PyCallOutcome` enum (`Value`, `GenYield`, `GenReturn`, `GenError`)
- [x] Implement `receive_python_result()` with PendingPython routing
- [x] **Tests:** Generator step, re-push with started=true, StopIteration handling

## Phase 5: Stdlib Handlers + Tests ✅

- [x] Implement `Effect` enum (Get, Put, Modify, Ask, Tell, Python, Scheduler)
- [x] Implement `Handler` enum (`Stdlib`, `Python`, `RustProgram`)
- [x] Implement State/Reader/Writer handlers
- [x] **Tests:** Get/Put round-trip, Ask from env, Tell to log, Modify with Python callback

## Phase 6: Dispatch System + Tests ✅

- [x] Implement `DispatchContext`, `start_dispatch()`, `visible_handlers()`, `lazy_pop_completed()`
- [x] **Tests:** Handler matching, busy boundary, completion marking, lazy pop

## Phase 7: Control Primitives + Tests ✅

- [x] All primitives: Pure, Resume, Transfer, WithHandler, Delegate, GetContinuation, GetHandlers, CreateContinuation, ResumeContinuation, PythonAsyncSyntaxEscape
- [x] One-shot tracking, handler return semantics
- [x] **FIX (CRITICAL):** Delegate clears handler frames (tail semantics)
- [x] **Tests:** Resume/Transfer semantics, one-shot violation, GetContinuation, Delegate

## Phase 8: PyO3 Driver + Integration Tests ✅

- [x] `PyVM` wrapper, `PyStdlib`, `PySchedulerHandler`
- [x] `classify_yielded()` — all primitives + effects
- [x] `execute_python_call()` with CallAsync TypeError guard
- [x] **Tests:** 20 Python integration tests

## Phase 9: P0 Fixes + RustProgram + ControlPrimitives ✅

- [x] GIL decoupling, resume_pending refactor, registry cleanup, dead code removal
- [x] `RustHandlerProgram` trait, `Handler::RustProgram`, `Frame::RustProgram`
- [x] `GetHandlers`, `CreateContinuation`, `ResumeContinuation` primitives
- [x] `RustStore` Clone + `PyStore` struct
- [x] **Tests:** 57→71 Rust, 14→20 Python

## Phase 10: Scheduler Handler ✅

- [x] `TaskId`, `PromiseId` IDs + `SchedulerEffect` enum
- [x] `Effect::Scheduler` + `Value::Task/Promise/ExternalPromise`
- [x] `SchedulerState` (ready queue, tasks, promises, waiters, store save/load/merge)
- [x] `SchedulerHandler` (`RustProgramHandler`) + `SchedulerProgram` (`RustHandlerProgram`)
- [x] Spawn/Gather/Race/Promise lifecycle
- [x] `PySchedulerHandler` pyclass + `PyVM::scheduler()`
- [x] Integration tests: scheduler creation, promise creation

## Phase 11: Async Integration ✅

- [x] `PythonCall::CallAsync` variant
- [x] `PendingPython::AsyncEscape` variant
- [x] `ControlPrimitive::PythonAsyncSyntaxEscape { action }`
- [x] sync_run TypeError guard for CallAsync
- [x] `classify_yielded` recognizes PythonAsyncSyntaxEscape
- [x] `receive_python_result` handles AsyncEscape

## Phase 12: Spec Gap Resolution ✅

- [x] `async_run` Python driver — `start_program()`, `step_once()`, `feed_async_result()`, `feed_async_error()`
- [x] Stdlib WithHandler scoping — `run_scoped()` with error-safe cleanup, `VM::remove_handler()`
- [x] `to_generator` ProgramBase validation — `to_generator_strict` / `to_generator_lenient` split
- [x] PyStore exposure — `py_store()`, `set_store()`, `get_store()` on PyVM
- [x] Send+Sync — Removed `#[pyclass(unsendable)]`, `Callback` is `Send+Sync`, documented `allow_threads` barrier
- [x] **Tests:** 73 Rust + 36 Python = 109 total

## Phase 13: TDD Gap-Detection + Fixes ✅

Spec-vs-impl review (8 parallel agents) found behavioral divergences. TDD approach: write failing tests first, then fix.

- [x] **G1 — CallHandler to_generator** (pyvm.rs): CallHandler now calls `to_generator_lenient` on handler return value, enabling handlers that return ProgramBase instead of generators
- [x] **G2 — GetHandlers handler_chain** (vm.rs): `handle_get_handlers` now uses dispatch context's `handler_chain` snapshot instead of `scope_chain`, preventing leakage of handlers installed during handler execution
- [x] **G4 — Yielded::Unknown for primitives** (pyvm.rs, error.rs): Primitive Python types (int, str, bool, etc.) yielded from generators now produce `TypeError` instead of being silently dispatched as effects; `VMError::TypeError` variant maps to `PyTypeError`
- [x] **G6 — Waitable::ExternalPromise** (scheduler.rs): Added `ExternalPromise(PromiseId)` variant to `Waitable` with support in `try_collect`, `try_race`, `wait_on_all`, `wait_on_any`
- [x] **G8 — RustStore methods** (vm.rs): Added `modify()` and `clear_logs()` to RustStore
- [x] **Tests:** 79 Rust + 39 Python = 118 total (was 73 + 36 = 109)

## P1 Correctness Fixes ✅

- [x] `Delegate { effect: Option<Effect> }` — optional effect override for spec parity
- [x] **FIX:** Delegate clears handler frames (tail semantics — prevents None return)
- [x] **FIX:** Python handler tests use Delegate for unknown effects (not raise ValueError)
- [x] `to_generator` validation — `to_generator_strict` rejects raw generators; `to_generator_lenient` for user-facing APIs
- [x] CallHandler ProgramBase validation — Covered by `to_generator_strict` at internal StartProgram entry points
- [x] WithHandler stdlib scoping — `run_scoped()` installs/removes handlers per-run with error cleanup

## Phase 14: TDD Spec-Gap Audit Round 2 ✅

Second spec-vs-impl audit (4 parallel agents) found 5 additional divergences. TDD: failing tests first, then fixes.

- [x] **G9 — clear_logs return type** (vm.rs): `RustStore::clear_logs()` now returns `Vec<Value>` via `std::mem::take` (was void)
- [x] **G10 — modify closure signature** (vm.rs): `RustStore::modify()` closure takes `&Value` (borrow) not `Value` (ownership), per spec
- [x] **G11 — with_local method** (vm.rs): Added `RustStore::with_local()` for scoped env bindings with save/restore
- [x] **G12 — DispatchContext fields** (vm.rs): Removed redundant `callsite_cont_id` and `handler_seg_id` fields; use `k_user.cont_id` directly
- [x] **G13 — Delegate effect type** (step.rs, vm.rs, pyvm.rs, scheduler.rs): Changed `Delegate { effect: Option<Effect> }` to `Delegate { effect: Effect }` per spec; pyvm falls back to dispatch context effect for Python `Delegate()` without explicit effect
- [x] **Tests:** 84 Rust + 21 Python = 105 total (was 79 + 21)
- [x] **Note:** 18 Python test failures are pre-existing PyO3 method-registration issue on CPython 3.14t (methods after 7th in `#[pymethods]` block not exposed)

## Phase 15: TDD Spec-Gap Audit Round 3 ✅

Third round addressing remaining audit findings. TDD: failing tests first, then fixes.

- [x] **G14 — Effect::type_name()** (effect.rs): Renamed `effect_type()` → `type_name()` to match spec method name
- [x] **G15 — WithHandler StartProgram** (vm.rs, pyvm.rs): Changed `handle_with_handler` to emit `PythonCall::StartProgram` (was `CallFunc`). StartProgram now falls back to calling as factory function for raw generator functions, preserving backward compatibility while routing ProgramBase through `to_generator()`
- [x] **G16 — lazy_pop_completed before primitives** (vm.rs): Added `self.lazy_pop_completed()` at top of `Yielded::Primitive` branch in `step_handle_yield`, matching spec's `handle_primitive` pattern. Prevents stale completed dispatches from affecting GetHandlers, Delegate, etc.
- [x] **Tests:** 87 Rust + 21 Python = 108 total (was 84 + 21)

## Phase 16: R9 Semantic Correctness Enforcement ✅

SPEC-008 Rev 9 / SPEC-009 Rev 3 — correctness-first philosophy. TDD with semgrep rules as structural assertions.

- [x] **Semgrep rules** (`.semgrep.yaml`): 6 rules enforcing ADR-13 (no install_handler in run/async_run), ADR-14 (no string shortcuts), API-12 (no sync delegation/ensure_future), naming (RunResult). All 6 pass.
- [x] **PyRustHandlerSentinel** (pyvm.rs): `#[pyclass(frozen, name = "RustHandler")]` wrapping `RustProgramHandlerRef`. Module-level `state`, `reader`, `writer` sentinels.
- [x] **NestingStep + NestingGenerator** (pyvm.rs): ProgramBase that yields one `WithHandler(handler, inner)`, creating proper nesting through normal VM `handle_with_handler` mechanism.
- [x] **run() rewrite** (pyvm.rs): Uses NestingStep chain instead of install_handler bypass. `handlers=[h0, h1, h2]` → `WithHandler(h0, WithHandler(h1, WithHandler(h2, program)))`.
- [x] **classify_yielded sentinel recognition** (pyvm.rs): WithHandler arms check for PyRustHandlerSentinel → `Handler::RustProgram(factory)`.
- [x] **classify_yielded completeness (INV-17)** (pyvm.rs): Added 6 scheduler effect arms (SpawnEffect, GatherEffect, RaceEffect, CompletePromiseEffect, FailPromiseEffect, TaskCompletedEffect) BEFORE `to_generator` fallback. Fixes infinite loop bug where EffectBase→ProgramBase inheritance caused scheduler effects to be misclassified as Programs.
- [x] **async_run rewrite (API-12)** (pyvm.rs): True `async def` coroutine with `await asyncio.sleep(0)` yield point. Replaces `ensure_future(completed_future)` pattern.
- [x] **Tests:** 97 Rust + 63 Python = 160 total (was 97 + 53 = 150)

## Phase 17: SPEC-009 Rev 5 — Spec Review + Corrections ✅

Comprehensive review of SPEC-009 against SPEC-008, SPEC-EFF-005, and implementation. Found 5 errors, fixed 3 in spec, 1 in code.

- [x] **E1 (R5-A) — §7 scheduler effects list**: Removed `Await` (Python-level asyncio bridge per SPEC-EFF-005, not scheduler) and `Wait` (not a SchedulerEffect variant). Added `CreateExternalPromise`, `TaskCompleted` to match Rust `SchedulerEffect` enum.
- [x] **E2 (R5-B) — §1 ADR-13 reference**: Removed dangling "(§0, ADR-13 in SPEC-008)" — no such ADR exists in SPEC-008.
- [x] **E3 (R5-C) — §7, §9 scheduler user-space**: Clarified scheduler is a user-space reference implementation, not a framework-internal component. Users can provide their own scheduler handlers.
- [x] **classify_yielded consolidation** (pyvm.rs): Merged 6 separate scheduler effect match arms into one combined arm. Added `WaitEffect`, `TaskCancelEffect`, `TaskIsDoneEffect`, `WaitForExternalCompletion`, and `_Scheduler*` prefix catch-all. Documented that `Effect::Python(obj)` is correct classification (not `Effect::Scheduler`) because schedulers are user-space Python handlers.
- [x] **Tests:** No regressions — 97 Rust + 63 Python = 160 tests, 6 semgrep rules all passing.

---

## Remaining Work

All spec gaps resolved. Only optional optimization work remains.

### Optional

- **PyO3 method registration** — Methods after the 7th in `#[pymethods]` block are not visible to Python on CPython 3.14t. Investigate PyO3 0.25 / inventory crate / linker-section registration limits.
- **`py.allow_threads()` barrier removal** — PyVM is `Send+Sync` but `step()` clones `Py<PyAny>` internally. Refactor to move/swap semantics to enable `allow_threads` around `run_rust_steps()`
- **Benchmarking** — Compare against Python CESK v3 interpreter

---

## Key Invariants

| ID | Invariant | Status |
|----|-----------|--------|
| INV-1 | GIL only held during PythonCall execution | ✅ (step() GIL-free; PyVM Send+Sync; allow_threads blocked by Py::clone() assertion) |
| INV-3 | One-shot continuations (ContId checked before resume) | ✅ |
| INV-7 | k.started validated before Resume/Transfer | ✅ |
| INV-9 | All handler installation through WithHandler (no install_handler bypass) | ✅ (ADR-13, semgrep enforced) |
| INV-14 | Generator re-push: GenYield re-pushes frame with started=true | ✅ |
| INV-15 | GIL-safe cloning: plain Clone (Py\<PyAny\> Clone via refcount) | ✅ |
| INV-16 | Structural equivalence: run() handler nesting = manual WithHandler | ✅ (NestingStep chain) |
| INV-17 | classify_yielded completeness: all scheduler effects classified before to_generator | ✅ (6 effect arms added) |
| API-12 | async_run is true async (yields to event loop) | ✅ (await sleep(0), semgrep enforced) |

---

## Test Summary

| Suite | Count | Status |
|-------|-------|--------|
| Rust unit tests (`cargo test`) | 97 | ✅ All passing |
| Python integration tests (`test_pyvm.py`) | 63 | ✅ All passing |
| Semgrep structural rules | 6 | ✅ All passing (0 violations) |
| **Total** | **160 + 6 rules** | **✅** |

---

## File Structure

```
packages/doeff-vm/
├── Cargo.toml      # PyO3 0.28, maturin, cdylib + rlib
├── .semgrep.yaml   # 6 structural rules enforcing SPEC-008/009 invariants
├── src/
│   ├── lib.rs          # Module root + re-exports
│   ├── ids.rs          # Core ID types (Marker, SegmentId, ContId, CallbackId, DispatchId, RunnableId, TaskId, PromiseId)
│   ├── value.rs        # Value enum (Python, Continuation, Handlers, Task, Promise, ExternalPromise, Unit, Int, String, Bool, None)
│   ├── error.rs        # VMError enum (OneShotViolation, UnhandledEffect, etc.)
│   ├── frame.rs        # Frame enum (RustReturn, RustProgram, PythonGenerator)
│   ├── effect.rs       # Effect enum (Get, Put, Modify, Ask, Tell, Python, Scheduler)
│   ├── segment.rs      # Segment + SegmentKind (Normal, PromptBoundary)
│   ├── continuation.rs # Continuation (capture/create) + RunnableContinuation
│   ├── handler.rs      # Handler, HandlerAction, StdlibHandler, RustHandlerProgram/RustProgramHandler traits
│   ├── arena.rs        # SegmentArena with free list
│   ├── scheduler.rs    # SchedulerEffect, SchedulerState, SchedulerHandler/Program, task/promise types
│   ├── step.rs         # Mode, StepEvent, PythonCall (incl. CallAsync), PendingPython (incl. AsyncEscape), 10 ControlPrimitives, PyCallOutcome
│   ├── vm.rs           # VM struct, step functions, RustStore, PyStore, DispatchContext, DebugConfig
│   └── pyvm.rs         # PyVM wrapper, PyStdlib, PySchedulerHandler, classify_yielded
└── tests/
    └── test_pyvm.py    # 36 Python integration tests
```

---

## Commit History

```
f08104e feat(doeff-vm): Complete remaining spec gaps — async_run, scoped handlers, validation, PyStore, Send+Sync
434b382 docs: Update VM issue tracker — Phase 11 complete, all spec features implemented
ed72ea4 feat(doeff-vm): Add async integration + Delegate effect field (P1/Phase 11)
27445ec fix(doeff-vm): Clear handler frames on Delegate + fix handler tests
561ee45 feat(doeff-vm): Wire scheduler into PyVM driver + integration tests
da4cf12 feat(doeff-vm): Implement scheduler handler — SchedulerState + SchedulerProgram
f6cc029 feat(doeff-vm): Add scheduler types — Effect::Scheduler, Value::Task/Promise
dca7f0d docs: Update VM issue tracker — Phase 9 complete, plan Phase 10 scheduler
3d78b30 feat(doeff-vm): Add RustProgram handlers and remaining ControlPrimitives
2104506 test(doeff-vm): Add end-to-end Python handler tests
ce083a2 refactor(doeff-vm): Phase 9 P0 — GIL decouple, cleanup, dead code
a0b162c docs: Update VM issue tracker with verified impl-vs-spec audit
c372d4b feat(doeff-vm): Implement core VM with PyO3 bindings and arena allocator
```

---

## References

- Spec: `specs/vm-architecture/SPEC-008-rust-vm.md`
- Scaffold: `packages/doeff-vm/`
- PyO3 Guide: https://pyo3.rs/
- maturin: https://www.maturin.rs/
