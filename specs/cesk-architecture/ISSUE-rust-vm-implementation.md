# Rust VM Implementation Plan

**Issue:** #235  
**Spec:** SPEC-CESK-008-rust-vm.md (Revision 7)  
**Status:** In Progress

## Overview

Implement a high-performance Rust VM with PyO3 integration, replacing the Python CESK v3 interpreter.

**Key Design Decisions:**
- 3-layer state model: `Internals` / `RustStore` / `PyStore`
- All effects go through dispatch (no bypass for stdlib)
- Mode-based step machine with `PendingPython` purpose tags
- Segment-based continuations with Arc snapshots

**Development Approach:** TDD - tests accompany each phase, not deferred to the end.

---

## Phase 1: Core Types + Tests

- [ ] Set up Rust crate with PyO3 and maturin (`packages/doeff-vm/`)
- [ ] Implement core IDs (`Marker`, `SegmentId`, `ContId`, `CallbackId`, `DispatchId`)
- [ ] Implement `Value` enum with Python interop (`Value::Python(Py<PyAny>)`)
- [ ] Implement `VMError` enum
- [ ] Implement `Frame` enum (`RustReturn`, `PythonGenerator`)
- [ ] **Tests:** ID uniqueness, Value Python round-trip, Frame clone behavior

## Phase 2: Continuation Structure + Tests

- [ ] Implement `Segment` and `SegmentKind` (Normal, PromptBoundary)
- [ ] Implement `Continuation` with `Arc<Vec<Frame>>` snapshots
- [ ] Implement `HandlerEntry` (handler + prompt_seg_id)
- [ ] Implement segment arena with free list
- [ ] **Tests:** Segment push/pop O(1), Continuation capture/materialize, arena alloc/free

## Phase 3: Step State Machine + Tests

- [ ] Implement `Mode` enum (`Deliver`, `Throw`, `HandleYield`, `Return`)
- [ ] Implement `StepEvent` enum (`Continue`, `NeedsPython`, `Done`, `Error`)
- [ ] Implement `PendingPython` enum (purpose tags for Python call routing)
- [ ] Implement `Yielded` enum and classification (in driver, with GIL)
- [ ] Implement `step()` main loop
- [ ] Implement `step_deliver_or_throw()` with generator frame handling
- [ ] Implement `step_handle_yield()` with pending_python setup
- [ ] Implement `step_return()` with caller traversal
- [ ] **Tests:** Mode transitions (unit), step returns correct StepEvent, caller chain traversal

## Phase 4: Python Call Protocol + Tests

- [ ] Implement `PythonCall` enum (`CallFunc`, `GenNext`, `GenSend`, `GenThrow`)
- [ ] Implement `PyCallOutcome` enum (`Value`, `GenYield`, `GenReturn`, `GenError`)
- [ ] Implement `receive_python_result()` with PendingPython routing
- [ ] Implement generator re-push rule (`started=true` after `GenYield`)
- [ ] Implement `PyException` wrapper
- [ ] **Tests:** Simple generator step (Python integration), re-push with started=true, StopIteration handling

## Phase 5: Stdlib Handlers + Tests

- [ ] Implement `Effect` enum (Get, Put, Modify, Ask, Tell, Python)
- [ ] Implement `Handler` enum (`Stdlib`, `Python`)
- [ ] Implement `StdlibHandler` enum (State, Reader, Writer)
- [ ] Implement `HandlerAction` enum (`Resume`, `Transfer`, `Return`, `NeedsPython`)
- [ ] Implement `StdStateHandler` with `continue_after_python()` for Modify
- [ ] Implement `StdReaderHandler` (Ask)
- [ ] Implement `StdWriterHandler` (Tell)
- [ ] **Tests:** Get/Put round-trip, Ask from env, Tell to log, Modify with Python callback

## Phase 6: Dispatch System + Tests

- [ ] Implement `DispatchContext` with handler_chain and handler_idx
- [ ] Implement `start_dispatch()` (new dispatch for perform-site effects)
- [ ] Implement `find_matching_handler()` returning (idx, marker, entry)
- [ ] Implement `visible_handlers()` with top-only busy boundary
- [ ] Implement dispatch completion detection via `callsite_cont_id`
- [ ] **Tests:** Handler matching by effect type, busy boundary exclusion, completion marking

## Phase 7: Control Primitives + Tests

- [ ] Implement `WithHandler` (prompt + body segment structure)
- [ ] Implement `Resume` (materialize snapshot, call-resume semantics)
- [ ] Implement `Transfer` (tail-transfer, no return link)
- [ ] Implement `Delegate` (advance handler_idx in SAME DispatchContext)
- [ ] Implement `GetContinuation`
- [ ] Implement one-shot tracking (`consumed_cont_ids`)
- [ ] **Tests:** WithHandler scope_chain setup, Resume returns to handler, Transfer abandons, Delegate advances idx, one-shot violation error

## Phase 8: PyO3 Driver + Integration Tests

- [ ] Implement `PyVM` wrapper struct
- [ ] Implement `run()` with GIL release during Rust steps
- [ ] Implement `execute_python_call()` dispatching to correct method
- [ ] Implement `step_generator()` with `Yielded::classify()` (GIL held)
- [ ] Implement Python API: `vm.stdlib()`, handler installation
- [ ] **Tests:** Full program execution from Python, nested handlers, exception propagation

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

---

## Key Invariants to Verify

| ID | Invariant | Tested In |
|----|-----------|-----------|
| INV-1 | GIL only held during PythonCall execution | Phase 8 |
| INV-3 | One-shot continuations (ContId checked before resume) | Phase 7 |
| INV-9 | All effects go through dispatch (no bypass) | Phase 6 |
| INV-14 | Generator protocol: GenYield re-pushes frame with started=true | Phase 4 |

---

## References

- Spec: `specs/cesk-architecture/SPEC-CESK-008-rust-vm.md`
- Scaffold: `packages/doeff-vm/`
- PyO3 Guide: https://pyo3.rs/
- maturin: https://www.maturin.rs/
