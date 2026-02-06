# Rust VM Implementation Plan

**Issue:** #235
**Spec:** SPEC-CESK-008-rust-vm.md (Revision 7)
**Status:** Phase 12 Complete — All spec gaps resolved, nothing deferred. 73 Rust + 36 Python = 109 tests passing.

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

## P1 Correctness Fixes ✅

- [x] `Delegate { effect: Option<Effect> }` — optional effect override for spec parity
- [x] **FIX:** Delegate clears handler frames (tail semantics — prevents None return)
- [x] **FIX:** Python handler tests use Delegate for unknown effects (not raise ValueError)
- [x] `to_generator` validation — `to_generator_strict` rejects raw generators; `to_generator_lenient` for user-facing APIs
- [x] CallHandler ProgramBase validation — Covered by `to_generator_strict` at internal StartProgram entry points
- [x] WithHandler stdlib scoping — `run_scoped()` installs/removes handlers per-run with error cleanup

---

## Remaining Work

All spec gaps resolved. Only optional optimization work remains.

### Optional

- **`py.allow_threads()` barrier removal** — PyVM is `Send+Sync` but `step()` clones `Py<PyAny>` internally. Refactor to move/swap semantics to enable `allow_threads` around `run_rust_steps()`
- **Benchmarking** — Compare against Python CESK v3 interpreter

---

## Key Invariants

| ID | Invariant | Status |
|----|-----------|--------|
| INV-1 | GIL only held during PythonCall execution | ✅ (step() GIL-free; PyVM Send+Sync; allow_threads blocked by Py::clone() assertion) |
| INV-3 | One-shot continuations (ContId checked before resume) | ✅ |
| INV-7 | k.started validated before Resume/Transfer | ✅ |
| INV-9 | All effects go through dispatch (no bypass) | ✅ |
| INV-14 | Generator re-push: GenYield re-pushes frame with started=true | ✅ |
| INV-15 | GIL-safe cloning: plain Clone (Py\<PyAny\> Clone via refcount) | ✅ |

---

## Test Summary

| Suite | Count | Status |
|-------|-------|--------|
| Rust unit tests (`cargo test`) | 73 | ✅ All passing |
| Python integration tests (`test_pyvm.py`) | 36 | ✅ All passing |
| **Total** | **109** | **✅** |

---

## File Structure

```
packages/doeff-vm/
├── Cargo.toml      # PyO3 0.25, maturin, cdylib + rlib
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

- Spec: `specs/cesk-architecture/SPEC-CESK-008-rust-vm.md`
- Scaffold: `packages/doeff-vm/`
- PyO3 Guide: https://pyo3.rs/
- maturin: https://www.maturin.rs/
