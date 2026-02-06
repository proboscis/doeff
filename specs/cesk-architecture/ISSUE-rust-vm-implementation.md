# Rust VM Implementation Plan

**Issue:** #235
**Spec:** SPEC-CESK-008-rust-vm.md (Revision 7)
**Status:** Phase 9 Complete — All control primitives + RustProgram handlers implemented. 57 Rust + 18 Python tests passing.

## Overview

Implement a high-performance Rust VM with PyO3 integration, replacing the Python CESK v3 interpreter.

**Key Design Decisions:**
- 3-layer state model: `Internals` / `RustStore` / `PyStore`
- All effects go through dispatch (no bypass for stdlib)
- Mode-based step machine with `PendingPython` purpose tags
- Segment-based continuations with Arc snapshots

**Development Approach:** TDD - tests accompany each phase, not deferred to the end.

---

## Implementation Status vs Spec (Verified 2026-02-06, Updated after Phase 9)

| Category | Status | Notes |
|----------|--------|-------|
| Core Types (IDs, Value, Frame, Segment) | ~98% | All present + `Value::Handlers`, `Frame::RustProgram`. Missing: `Value::Task/Promise/ExternalPromise` |
| Continuation & Arena | 100% | Fully aligned with spec |
| Effect & Handler | ~95% | `Handler::RustProgram` + `RustHandlerProgram` trait + `RustProgramHandler` factory. Missing: `Effect::Scheduler` |
| Step Machine (Mode, StepEvent, Yielded) | ~95% | Working. Missing: `PythonCall::CallAsync`, `PendingPython::AsyncEscape` |
| Dispatch System | 100% | `start_dispatch`, `visible_handlers`, `lazy_pop_completed` all correct |
| Control Primitives | ~90% | 9/10 implemented. Missing: `PythonAsyncSyntaxEscape` |
| PyO3 Driver | ~90% | GIL-decoupled (`step()` no `py` param). `PyCallOutcome::Value(Value)`. Missing: `allow_threads` (requires Send), `CallAsync` |
| Stdlib Handlers | ~95% | Semantics correct. `RustStore` is `Clone`. Pre-installed globally (not WithHandler-scoped). |
| Scheduler | 0% | Not started — types, handler, state management all needed |
| PyStore (Layer 3) | ~50% | `PyStore` struct exists with `Py<PyDict>`. Not exposed to handlers yet. |
| Async Integration | 0% | No `CallAsync`, no `async_run`, no `PythonAsyncSyntaxEscape` |

---

## Phase 1: Core Types + Tests ✅

- [x] Set up Rust crate with PyO3 0.25 and maturin (`packages/doeff-vm/`)
- [x] Implement core IDs (`Marker`, `SegmentId`, `ContId`, `CallbackId`, `DispatchId`, `RunnableId`)
- [x] Implement `Value` enum with Python interop (`Value::Python(Py<PyAny>)`)
- [x] Implement `VMError` enum
- [x] Implement `Frame` enum (`RustReturn`, `PythonGenerator`) - uses CallbackId for Clone support
- [x] **Tests:** ID uniqueness, Value accessors, Frame clone behavior
- **Minor divergence:** `CallbackId(u64)` vs spec's `CallbackId(u32)` — no functional impact
- **Dead code:** `frame.rs` contains unused `PythonCallPurpose` and `PythonCall` struct (the real `PythonCall` is in `step.rs`)

## Phase 2: Continuation Structure + Tests ✅

- [x] Implement `Segment` with frames, caller, scope_chain
- [x] Implement `SegmentKind` enum (Normal, PromptBoundary)
- [x] Implement `Continuation` with `Arc<Vec<Frame>>` snapshots (capture + create)
- [x] Implement `Continuation::create()` for unstarted continuations
- [x] Implement `Continuation::with_id()` for pre-assigned ContId
- [x] Implement `HandlerEntry` (handler + prompt_seg_id)
- [x] Implement segment arena with free list (`arena.rs`)
- [x] Implement `RunnableContinuation` (internal to scheduler)
- [x] **Tests:** Segment push/pop O(1), Continuation capture/materialize, arena alloc/free, snapshot independence

## Phase 3: Step State Machine + Tests ✅

- [x] Implement `Mode` enum (`Deliver`, `Throw`, `HandleYield`, `Return`)
- [x] Implement `StepEvent` enum (`Continue`, `NeedsPython`, `Done`, `Error`)
- [x] Implement `PendingPython` enum (`StartProgramFrame`, `StepUserGenerator`, `CallPythonHandler`, `StdlibContinuation`)
- [x] Implement `Yielded` enum (Primitive, Effect, Program, Unknown)
- [x] Implement `ControlPrimitive` enum (Resume, Transfer, WithHandler, Delegate, GetContinuation, GetHandlers, CreateContinuation, ResumeContinuation, Pure)
- [x] Implement `step()` main loop — dispatches to step_deliver_or_throw / step_handle_yield / step_return
- [x] Implement `step_deliver_or_throw()` with RustReturn callback and PythonGenerator handling
- [x] Implement `step_handle_yield()` — routes Primitive/Effect/Program/Unknown
- [x] Implement `step_return()` with caller traversal
- [x] **Tests:** Mode transitions (unit), step returns correct StepEvent, caller chain traversal

## Phase 4: Python Call Protocol + Tests ✅

- [x] Implement `PythonCall` enum (`StartProgram`, `CallFunc`, `CallHandler`, `GenNext`, `GenSend`, `GenThrow`)
- [x] Implement `PyCallOutcome` enum (`Value`, `GenYield`, `GenReturn`, `GenError`)
- [x] Implement `receive_python_result()` with PendingPython routing
- [x] Implement generator re-push rule (`started=true` after `GenYield`)
- [x] Implement `PyException` wrapper with clone_ref support
- [x] **Tests:** Generator step, re-push with started=true, StopIteration handling

## Phase 5: Stdlib Handlers + Tests ✅

- [x] Implement `Effect` enum (Get, Put, Modify, Ask, Tell, Python)
- [x] Implement `Handler` enum (`Stdlib`, `Python`)
- [x] Implement `StdlibHandler` enum (State, Reader, Writer) — unit variants with methods on enum
- [x] Implement `HandlerAction` enum (`Resume`, `Transfer`, `Return`, `NeedsPython`)
- [x] Implement `HandlerContext::ModifyPending`
- [x] Implement State handler: Get (lookup), Put (insert), Modify (NeedsPython + continue_after_python)
- [x] Implement Reader handler: Ask (env lookup)
- [x] Implement Writer handler: Tell (log append)
- [x] `Handler::Python.can_handle()` returns true for all effects (correct per spec)
- [x] **Tests:** Get/Put round-trip, Ask from env, Tell to log, Modify with Python callback, handler matching
- **Divergence:** Spec has `StdlibHandler::State(StdStateHandler)` (inner struct); impl has `StdlibHandler::State` (unit variant). Functionally equivalent.

## Phase 6: Dispatch System + Tests ✅

- [x] Implement `DispatchContext` with handler_chain, handler_idx, k_user, prompt_seg_id, completed
- [x] Implement `start_dispatch()` — captures k_user, allocates handler segment, dispatches to handler
- [x] Implement `find_matching_handler()` returning (idx, marker, entry)
- [x] Implement `visible_handlers()` with top-only busy boundary
- [x] Implement `lazy_pop_completed()` for dispatch stack cleanup
- [x] Implement dispatch completion detection via `callsite_cont_id`
- [x] **Tests:** Handler matching by effect type, busy boundary exclusion, completion marking, lazy pop
- **Extra fields in DispatchContext** (not in spec): `callsite_cont_id` (derived from k_user), `handler_seg_id` (tracks handler segment), `resume_pending`/`resume_value` (non-spec handler return mechanism)

## Phase 7: Control Primitives + Tests ✅

- [x] `Pure` — delivers value directly (no dispatch)
- [x] `WithHandler` — creates PromptBoundary segment, installs handler, starts body
- [x] `Resume` — one-shot check, k.started check, materializes snapshot, caller = current segment (call-resume)
- [x] `Transfer` — one-shot check, k.started check, materializes snapshot, caller = None (tail-transfer)
- [x] `Delegate` — advances handler_idx in dispatch stack, finds next matching handler, caller = inner_seg_id
- [x] `GetContinuation` — returns k_user from dispatch context, registers in continuation_registry
- [x] One-shot tracking via `consumed_cont_ids`
- [x] `handle_handler_return()` — implicit Return semantics for handler programs
- [x] **FIX:** `k.started` validation on Resume/Transfer
- [x] **FIX:** `lazy_pop_completed()` before dispatch completion check
- [x] **FIX (CRITICAL):** Delegate outer handler segment `caller = inner_seg_id` (was prompt_seg_id)
- [x] **FIX:** Delegate updates `top.effect` per spec
- [x] **FIX:** GetContinuation restriction removed (spec only requires dispatch context)
- [x] **Tests:** Resume call-resume semantics, Transfer tail semantics, one-shot violation error, GetContinuation, Delegate errors

## Phase 8: PyO3 Driver + Integration Tests ✅

- [x] `PyVM` wrapper struct (unsendable)
- [x] `PyVM::run()` — driver loop (step → NeedsPython → execute → receive → repeat)
- [x] `execute_python_call()` — dispatches all PythonCall variants
- [x] `step_generator()` — handles GenYield/GenReturn/GenError via __next__/send/throw
- [x] `classify_yielded()` — GIL-held classification: Pure, Resume, Transfer, WithHandler, Delegate, GetContinuation, GetHandlers, CreateContinuation, ResumeContinuation, all effects, Programs
- [x] `to_generator()` — converts ProgramBase to generator (via to_generator method)
- [x] `PyStdlib` — install_state/install_reader/install_writer
- [x] `PyVM::state_items()` and `PyVM::logs()` — observe stdlib state
- [x] `PyVM::enable_debug()` — steps/trace modes
- [x] **Tests:** 14 Python integration tests (pure, state, writer, modify, combined, Python handlers, custom effects)

## Phase 8.5: Spec Alignment Review ✅ COMPLETE

### 8.5.1 Segment & Continuation — Aligned ✅
- `current_segment` is `Option<SegmentId>` vs spec's `SegmentId` — minor, prevents panics on empty VM

### 8.5.2 Python Call Protocol — Structural Gaps (Deferred)
- **PyCallOutcome::Value(Py\<PyAny\>)** — spec requires `Value(Value)`. Driver should convert with GIL held. This forces `step()` to take `py` param.
- **No PythonCall::CallAsync** — needed for async integration
- **No PendingPython::AsyncEscape** — needed for async integration
- **to_generator accepts raw generators** — spec says reject for ProgramBase entry points
- **CallHandler doesn't validate ProgramBase** — handler result should be validated

### 8.5.3 Stdlib Handler — Aligned ✅
- Semantics correct. `StdlibHandler` uses unit variants instead of inner structs (functionally equivalent).
- `RustStore` not `Clone` — needed for scheduler's `StoreMode::Isolated`.

### 8.5.4 Control Primitives — 9/10 Implemented ✅ (Phase 9)
Implemented: `Pure`, `Resume`, `Transfer`, `WithHandler`, `Delegate`, `GetContinuation`, `GetHandlers`, `CreateContinuation`, `ResumeContinuation`
Missing:
- `PythonAsyncSyntaxEscape` — request async execution in handler (deferred to async integration)

`Delegate` is no-arg (reuses top dispatch effect). Spec has `Delegate { effect }` variant. Current impl updates `top.effect` correctly so behavior matches, but signature diverges.

### 8.5.5 Handler Return — Non-Spec Mechanism ⚠️
- `resume_pending`/`resume_value` fields removed from DispatchContext (Phase 9 P0)
- Handler return handled via `handle_handler_return()` callback on RustReturn frame
- Works correctly for current test cases

### 8.5.6 GIL Coupling — Mostly Resolved ✅ (Phase 9)
- `step()` no longer takes `py: Python<'_>` parameter ✅
- `PyCallOutcome::Value(Value)` — driver converts while holding GIL ✅
- `Value::clone_ref(py)` → plain `.clone()` ✅
- Remaining: `py.allow_threads()` deferred — PyVM is `#[pyclass(unsendable)]`, `&mut self` not `Send`

### 8.5.7 Continuation Registry — Lifecycle Managed ✅ (Phase 9)
- `mark_one_shot_consumed()` removes entries from `continuation_registry`
- Memory bounded by live continuations only

### 8.5.8 Stdlib Installation — Global Pre-Install ⚠️
- `PyStdlib::install_*()` pre-installs handlers globally with dedicated prompt segments
- Spec envisions stdlib installed via `WithHandler` like any other handler
- Current approach works but handlers aren't scoped — they persist across runs on same VM

### 8.5.9 RustProgram Handlers — Fully Implemented ✅ (Phase 9)
- `RustHandlerProgram` trait (start/resume/throw generator protocol)
- `RustProgramHandler` factory trait + `Arc` type aliases
- `Handler::RustProgram` variant with `can_handle` dispatch
- `Frame::RustProgram` variant for continuation stack
- `apply_rust_program_step()` routes Yield/Return/Throw
- Dispatch and Delegate both wire through RustProgram `.start()`

---

## Remaining Work (Prioritized)

### P0 — Completed ✅

1. ~~**GIL decoupling**~~ ✅ — `step()` no longer takes `py`, `PyCallOutcome::Value(Value)`, `clone_ref` → `.clone()`
2. ~~**`resume_pending`/`resume_value` refactor**~~ ✅ — Removed; handler return flows through RustReturn callback
3. ~~**Continuation registry cleanup**~~ ✅ — `mark_one_shot_consumed()` removes from registry
4. ~~**Dead code cleanup**~~ ✅ — PythonCallPurpose/PythonCall removed from frame.rs

### P1 — Should Fix (correctness edge cases)

5. **`to_generator` validation** — Reject raw generators at ProgramBase entry points (StartProgram, CallHandler, Yielded::Program). Only `start_with_generator()` should accept raw generators.

6. **CallHandler ProgramBase validation** — Validate handler result is a ProgramBase before converting to generator.

7. **Delegate `{effect}` field** — Add optional effect field to ControlPrimitive::Delegate for spec parity. Current behavior (reusing top dispatch effect) is correct but implicit.

8. **WithHandler stdlib scoping** — Refactor `PyStdlib` to return handler objects usable with `WithHandler` instead of global pre-installation.

### P2 — Completed ✅

9. ~~**`RustStore` Clone**~~ ✅ — Derives Clone
10. ~~**`GetHandlers`**~~ ✅ — Returns handler chain from scope
11. ~~**`CreateContinuation`**~~ ✅ — Creates unstarted continuation with handlers
12. ~~**`ResumeContinuation`**~~ ✅ — Handles started + unstarted continuations
13. ~~**`Handler::RustProgram`**~~ ✅ — Full type system + dispatch integration

### P3 — Scheduler + Extensions (Phase 10-11)

14. **Scheduler types** — `SchedulerEffect` enum, `TaskId`/`PromiseId`/`Waitable` IDs, `StoreMode`/`StoreMergePolicy`, `TaskStore`/`TaskState`/`PromiseState`
15. **`Effect::Scheduler`** variant + `Value::Task/Promise/ExternalPromise` + PyO3 wrappers
16. **`SchedulerState`** — Ready queue, tasks, promises, waiters, store save/load/merge
17. **`SchedulerHandler`** (`RustProgramHandler`) + **`SchedulerProgram`** (`RustHandlerProgram`) — Spawn/Gather/Race/Promise, Transfer-only semantics
18. **`PythonAsyncSyntaxEscape`** + `PythonCall::CallAsync` + `PendingPython::AsyncEscape` + `async_run` driver
19. **`PythonCall::CallAsync`** + sync_run TypeError guard

---

## Phase 9: P0 Fixes + RustProgram + ControlPrimitives ✅

- [x] Fix P0 items (GIL decoupling, resume_pending refactor, registry cleanup, dead code)
- [x] Add `RustHandlerProgram` trait + `RustProgramHandler` factory + type aliases
- [x] Add `Handler::RustProgram` variant with dispatch integration
- [x] Add `Frame::RustProgram` variant + `apply_rust_program_step()`
- [x] Add `Value::Handlers` variant + `GetHandlers` primitive
- [x] Add `CreateContinuation` + `ResumeContinuation` primitives
- [x] Add `RustStore` Clone + `PyStore` struct
- [x] pyvm.rs: classify_yielded support for all new primitives
- [x] Test complex nested handler scenarios (handler-in-handler, delegate chains)
- [x] Test abandon semantics (handler returns without Resume)
- [x] Test exception propagation through handler chains
- [x] **Tests:** 57 Rust unit tests + 18 Python integration tests

## Phase 10: Scheduler Handler (Next)

### 10.1 Scheduler Types
- [ ] Add `TaskId`, `PromiseId`, `Waitable` ID types to `ids.rs`
- [ ] Add `SchedulerEffect` enum to `effect.rs`
- [ ] Add `Effect::Scheduler` variant
- [ ] Add `StoreMode`, `StoreMergePolicy`, `TaskStore` types
- [ ] Add `Value::Task(TaskHandle)`, `Value::Promise(PromiseHandle)`, `Value::ExternalPromise(ExternalPromise)`
- [ ] Add `TaskHandle`, `PromiseHandle`, `ExternalPromise` structs with PyO3 wrappers

### 10.2 Scheduler State + Handler
- [ ] Implement `SchedulerState` (ready queue, tasks, promises, waiters)
- [ ] Implement `TaskState`, `PromiseState` enums
- [ ] Implement `SchedulerHandler` (`RustProgramHandler` — factory)
- [ ] Implement `SchedulerProgram` (`RustHandlerProgram` — start/resume/throw)
- [ ] Implement Spawn (CreateContinuation → enqueue task → Transfer)
- [ ] Implement Gather/Race (wait or immediate → Transfer)
- [ ] Implement Promise lifecycle (Create/Complete/Fail)
- [ ] Implement TaskCompleted (mark done, wake waiters)
- [ ] Implement store save/load/merge for isolated tasks

### 10.3 Integration
- [ ] Fix P1 items (validation, stdlib scoping)
- [ ] Wire `PyVM::scheduler()` to return PyO3-wrapped handler
- [ ] Integration tests: spawn, gather, race, promises
- [ ] Benchmark against Python CESK v3 interpreter

## Phase 11: Async Integration (Optional)

- [ ] Add `PythonCall::CallAsync` variant
- [ ] Add `PendingPython::AsyncEscape` variant
- [ ] Add `ControlPrimitive::PythonAsyncSyntaxEscape`
- [ ] Implement `async_run` / `VM.run_async` Python driver
- [ ] Guard: sync_run raises TypeError on CallAsync

---

## Key Invariants

| ID | Invariant | Status |
|----|-----------|--------|
| INV-1 | GIL only held during PythonCall execution | ✅ (step() GIL-free; allow_threads deferred due to unsendable) |
| INV-3 | One-shot continuations (ContId checked before resume) | ✅ |
| INV-7 | k.started validated before Resume/Transfer | ✅ |
| INV-9 | All effects go through dispatch (no bypass) | ✅ |
| INV-14 | Generator re-push: GenYield re-pushes frame with started=true | ✅ |
| INV-15 | GIL-safe cloning: plain Clone (Py\<PyAny\> Clone via refcount) | ✅ |

---

## Test Summary

| Suite | Count | Status |
|-------|-------|--------|
| Rust unit tests (`cargo test`) | 57 | ✅ All passing |
| Python integration tests (`test_pyvm.py`) | 18 | ✅ All passing |
| **Total** | **75** | **✅** |

---

## File Structure

```
packages/doeff-vm/
├── Cargo.toml      # PyO3 0.25, maturin, cdylib + rlib
├── src/
│   ├── lib.rs          # Module root + re-exports
│   ├── ids.rs          # Core ID types (Marker, SegmentId, ContId, CallbackId, DispatchId, RunnableId)
│   ├── value.rs        # Value enum (Python, Continuation, Handlers, Unit, Int, String, Bool, None)
│   ├── error.rs        # VMError enum (OneShotViolation, UnhandledEffect, etc.)
│   ├── frame.rs        # Frame enum (RustReturn, RustProgram, PythonGenerator)
│   ├── effect.rs       # Effect enum (Get, Put, Modify, Ask, Tell, Python)
│   ├── segment.rs      # Segment + SegmentKind (Normal, PromptBoundary)
│   ├── continuation.rs # Continuation (capture/create) + RunnableContinuation
│   ├── handler.rs      # Handler, HandlerAction, StdlibHandler, RustHandlerProgram trait, RustProgramHandler trait
│   ├── arena.rs        # SegmentArena with free list
│   ├── step.rs         # Mode, StepEvent, PythonCall, PendingPython, Yielded, 9 ControlPrimitives, PyCallOutcome
│   ├── vm.rs           # VM struct, step functions, RustStore, PyStore, DispatchContext, DebugConfig
│   └── pyvm.rs         # PyVM wrapper, PyStdlib, Python bindings, classify_yielded
└── tests/
    └── test_pyvm.py    # 14 Python integration tests
```

---

## References

- Spec: `specs/cesk-architecture/SPEC-CESK-008-rust-vm.md`
- Scaffold: `packages/doeff-vm/`
- PyO3 Guide: https://pyo3.rs/
- maturin: https://www.maturin.rs/
