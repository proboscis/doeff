# Rust VM Implementation Plan

**Issue:** #235  
**Spec:** SPEC-CESK-008-rust-vm.md (Revision 7)  
**Status:** Phase 8.5 Complete — All reviews done, critical fixes applied, 66/66 tests passing.

## Overview

Implement a high-performance Rust VM with PyO3 integration, replacing the Python CESK v3 interpreter.

**Key Design Decisions:**
- 3-layer state model: `Internals` / `RustStore` / `PyStore`
- All effects go through dispatch (no bypass for stdlib)
- Mode-based step machine with `PendingPython` purpose tags
- Segment-based continuations with Arc snapshots

**Development Approach:** TDD - tests accompany each phase, not deferred to the end.

---

## Implementation Status vs Spec

| Category | Status | Notes |
|----------|--------|-------|
| Core VM (Phases 1-6) | ~98% | All structures present, tests passing |
| Control Primitives (Phase 7) | ~92% | Fixes applied: delegate caller, k.started, lazy_pop, GetCont restriction |
| PyO3 Integration (Phase 8) | ~88% | 14 Python tests passing; GIL/PyCallOutcome structural gaps deferred |
| Spec Extensions | 0% | RustProgram handlers, Scheduler, missing ControlPrimitive variants |

---

## Phase 1: Core Types + Tests ✅

- [x] Set up Rust crate with PyO3 and maturin (`packages/doeff-vm/`)
- [x] Implement core IDs (`Marker`, `SegmentId`, `ContId`, `CallbackId`, `DispatchId`, `RunnableId`)
- [x] Implement `Value` enum with Python interop (`Value::Python(Py<PyAny>)`)
- [x] Implement `VMError` enum
- [x] Implement `Frame` enum (`RustReturn`, `PythonGenerator`) - uses CallbackId for Clone support
- [x] **Tests:** ID uniqueness, Value accessors, Frame clone behavior

## Phase 2: Continuation Structure + Tests ✅

- [x] Implement `Segment` with frames, caller, scope_chain
- [x] Implement `SegmentKind` enum (Normal, PromptBoundary)
- [x] Implement `Continuation` with `Arc<Vec<Frame>>` snapshots (capture)
- [x] Implement `Continuation::create()` for unstarted continuations
- [x] Implement `HandlerEntry` (handler + prompt_seg_id)
- [x] Implement segment arena with free list
- [x] **Tests:** Segment push/pop O(1), Continuation capture/materialize, arena alloc/free

## Phase 3: Step State Machine + Tests ✅

- [x] Implement `Mode` enum (`Deliver`, `Throw`, `HandleYield`, `Return`)
- [x] Implement `StepEvent` enum (`Continue`, `NeedsPython`, `Done`, `Error`)
- [x] Implement `PendingPython` enum (basic: `StartProgramFrame`, `StepUserGenerator`)
- [x] Implement `Yielded` enum and classification (in driver, with GIL)
- [x] Implement `step()` main loop
- [x] Implement `step_deliver_or_throw()` with generator frame handling
- [x] Implement `step_handle_yield()` with pending_python setup
- [x] Implement `step_return()` with caller traversal
- [x] **Tests:** Mode transitions (unit), step returns correct StepEvent, caller chain traversal

## Phase 4: Python Call Protocol + Tests ✅

- [x] Implement `PythonCall` enum (`CallFunc`, `GenNext`, `GenSend`, `GenThrow`)
- [x] Implement `PythonCall::StartProgram`
- [x] Implement `PythonCall::CallHandler`
- [x] Implement `PyCallOutcome` enum (`Value`, `GenYield`, `GenReturn`, `GenError`)
- [x] Implement `receive_python_result()` with PendingPython routing
- [x] Implement `PendingPython::CallPythonHandler` routing
- [x] Implement `PendingPython::StdlibContinuation` routing
- [x] Implement generator re-push rule (`started=true` after `GenYield`)
- [x] Implement `PyException` wrapper
- [x] **Tests:** Generator step, re-push with started=true, StopIteration handling

## Phase 5: Stdlib Handlers + Tests ✅

- [x] Implement `Effect` enum (Get, Put, Modify, Ask, Tell, Python)
- [x] Implement `Handler` enum (`Stdlib`, `Python`)
- [ ] Implement `Handler::RustProgram` variant - **GAP: optional, deferred to Phase 11**
- [x] Implement `StdlibHandler` enum (State, Reader, Writer)
- [x] Implement `HandlerAction` enum (`Resume`, `Transfer`, `Return`, `NeedsPython`)
- [x] Implement `StdStateHandler` (Get, Put work)
- [x] Implement `StdStateHandler::continue_after_python()` for Modify
- [x] Implement `StdReaderHandler` (Ask)
- [x] Implement `StdWriterHandler` (Tell)
- [x] **Tests:** Get/Put round-trip, Ask from env, Tell to log
- [x] **Tests:** Modify with Python callback

## Phase 6: Dispatch System + Tests ✅

- [x] Implement `DispatchContext` with handler_chain and handler_idx
- [x] Implement `start_dispatch()` (new dispatch for perform-site effects)
- [x] Implement `find_matching_handler()` returning (idx, marker, entry)
- [x] Implement `visible_handlers()` with top-only busy boundary
- [x] Implement dispatch completion detection via `callsite_cont_id`
- [x] **Tests:** Handler matching by effect type, busy boundary exclusion, completion marking

## Phase 7: Control Primitives + Tests ✅

- [x] Implement `Pure` (re-pushes generator frame to resume with value)
- [x] Implement `WithHandler` with PromptBoundary segment
- [x] Implement `Resume` (materialize snapshot, call-resume semantics)
- [x] Implement `Transfer` (tail-transfer, no return link)
- [x] Implement `Delegate` (advance handler_idx)
- [x] Implement `GetContinuation`
- [x] Implement one-shot tracking (`consumed_cont_ids`)
- [x] **FIX:** `k.started` validation added to `handle_resume` and `handle_transfer`
- [x] **FIX:** `lazy_pop_completed()` added before dispatch completion check
- [x] **FIX (CRITICAL):** Delegate outer handler segment `caller = inner_seg_id` (was prompt_seg_id)
- [x] **FIX:** Delegate now updates `top.effect` per spec
- [x] **FIX:** GetContinuation handler_seg_id restriction removed (spec only requires dispatch context)
- [x] **Tests:** Resume returns to caller, Transfer abandons caller, one-shot violation error (52 Rust tests)

## Phase 8: PyO3 Driver + Integration Tests ✅

- [x] Implement `PyVM` wrapper struct
- [x] Implement `run()` driver loop
- [x] Implement `execute_python_call()` dispatching to correct method
- [x] Implement `step_generator()` with classify_yielded (GIL held)
- [x] Implement Python API: `vm.stdlib()`, handler installation
- [x] Implement Python handler invocation path
- [x] **FIX:** `Handler::Python.can_handle` returns true for all effects (was restricted to Effect::Python)
- [x] **FIX:** Removed temporary `test_method` from PyVM
- [x] **Tests:** 14 Python integration tests passing (pure, state, writer, modify, combined, Python handlers)

## Phase 8.5: Spec Alignment ✅ COMPLETE

### 8.5.1 Segment & Continuation — Aligned ✅
Minor: `current_segment` is `Option<SegmentId>` vs spec's `SegmentId`.

### 8.5.2 Python Call Protocol — Structural Gaps (Deferred)
- `PyCallOutcome::Value` contains `Py<PyAny>` not `Value` — requires GIL refactor
- No `CallAsync` variant — needs async integration
- `to_generator` accepts raw generators — spec requires ProgramBase validation
- `CallHandler` doesn't validate result as ProgramBase

### 8.5.3 Stdlib Handler — Aligned ✅
- Semantics correct. `RustStore` not `Clone` yet (needed for scheduler's StoreMode::Isolated).

### 8.5.4 WithHandler & Control Primitives — Partial
- Missing 5 `ControlPrimitive` variants: `GetHandlers`, `CreateContinuation`, `ResumeContinuation`, `PythonAsyncSyntaxEscape`, `Delegate{effect}`
- `resume_pending`/`resume_value` in `handle_handler_return` diverges from spec (spec uses handler value directly)

### 8.5.5 Python Handler Invocation — Aligned ✅
- No ProgramBase validation on handler result. Continuation serialized as dict.

---

## Remaining Divergences (Prioritized)

### Must Fix (before Phase 10 integration)
1. **PyCallOutcome type**: `Value(Py<PyAny>)` → `Value(Value)` — driver should convert while holding GIL
2. **GIL release**: `step()` takes `py: Python<'_>` param; spec says step runs without GIL
3. **resume_pending/resume_value**: Custom mechanism not in spec; semantics differ for handler return
4. **Continuation registry lifecycle**: No cleanup/GC for registered continuations

### Should Fix (correctness edge cases)
5. **to_generator validation**: Reject raw generators; require ProgramBase
6. **CallHandler ProgramBase validation**: Handler result should be validated
7. **Delegate ControlPrimitive**: Should carry `effect` field (currently no-arg, reuses top dispatch)
8. **Stdlib installation**: Pre-installed globally instead of WithHandler-scoped

### Nice to Have (Phase 11)
9. Missing ControlPrimitive variants (GetHandlers, CreateContinuation, ResumeContinuation, PythonAsyncSyntaxEscape)
10. Handler::RustProgram variant
11. Scheduler handler
12. PyStore Layer 3
13. Debug API: `enable_debug(str)` → `set_debug(DebugConfig)`

---

## Phase 9: End-to-End & Performance

- [ ] Test complex nested handler scenarios
- [ ] Test abandon semantics (handler returns without Resume)
- [ ] Benchmark against Python interpreter
- [ ] Memory leak checks (PyObject reference counting)

## Phase 10: Integration & Migration

- [ ] Integrate with existing doeff Python API
- [ ] Migrate `@do` decorator support
- [ ] Ensure backward compatibility with existing programs
- [ ] Documentation and examples

## Phase 11: Optional Extensions

### 11.1 RustProgram Handlers (Optional)
- [ ] Add `Frame::RustProgram` variant
- [ ] Add `Handler::RustProgram` variant
- [ ] Implement `RustHandlerProgram` trait
- [ ] Implement `RustProgramHandler` factory trait
- [ ] Add `RustProgramStep` enum (Yield, Return, Throw)

### 11.2 Scheduler Handler (Optional)
- [ ] Add `SchedulerEffect` enum (Spawn, Gather, Race, etc.)
- [ ] Add `Value::Task`, `Value::Promise`, `Value::ExternalPromise`
- [ ] Implement `SchedulerHandler` with task/promise state
- [ ] Add PyO3 wrappers for TaskHandle, PromiseHandle

### 11.3 PyStore Layer 3 (Optional)
- [ ] Implement `PyStore` struct with Python dict
- [ ] Add to VM struct
- [ ] Expose via PyO3 for Python handlers

---

## Key Invariants

| ID | Invariant | Status |
|----|-----------|--------|
| INV-1 | GIL only held during PythonCall execution | ⚠️ Partially (step() still takes py param) |
| INV-3 | One-shot continuations (ContId checked before resume) | ✅ |
| INV-7 | k.started validated before Resume/Transfer | ✅ (fixed) |
| INV-9 | All effects go through dispatch (no bypass) | ✅ |
| INV-14 | Generator protocol: GenYield re-pushes frame with started=true (except Pure) | ✅ |
| INV-15 | GIL-safe cloning: use clone_ref(py) for Py<PyAny>, not Clone trait | ✅ |

---

## Test Summary

| Suite | Count | Status |
|-------|-------|--------|
| Rust unit tests (`cargo test`) | 52 | ✅ All passing |
| Python integration tests (`test_pyvm.py`) | 14 | ✅ All passing |
| **Total** | **66** | **✅** |

---

## File Structure

```
packages/doeff-vm/src/
├── lib.rs          # Module root + re-exports
├── ids.rs          # Core ID types (Marker, SegmentId, ContId, etc.)
├── value.rs        # Value enum with Python interop
├── error.rs        # VMError enum
├── frame.rs        # Frame enum (RustReturn, PythonGenerator)
├── effect.rs       # Effect enum (Get, Put, Modify, Ask, Tell, Python)
├── segment.rs      # Segment + SegmentKind
├── continuation.rs # Continuation with Arc snapshots
├── handler.rs      # Handler, HandlerAction, stdlib handlers
├── arena.rs        # SegmentArena with free list
├── step.rs         # Mode, StepEvent, PendingPython, Yielded, etc.
├── vm.rs           # VM struct, step functions, RustStore, DispatchContext
└── pyvm.rs         # PyVM wrapper, Python bindings
```

---

## References

- Spec: `specs/cesk-architecture/SPEC-CESK-008-rust-vm.md`
- Scaffold: `packages/doeff-vm/`
- PyO3 Guide: https://pyo3.rs/
- maturin: https://www.maturin.rs/
