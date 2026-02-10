# SA-007 Phase 1 Tasks

Team: `spec-audit-SA-007`
Session: `SA-007`

| Task ID | Section | Spec file | Line range | Target implementation files |
|---|---|---|---|---|
| P1-U1 | SPEC-008 foundations and Rust data structures | `specs/vm/SPEC-008-rust-vm.md` | `95-1215` | `packages/doeff-vm/src/ids.rs`, `packages/doeff-vm/src/py_shared.rs`, `packages/doeff-vm/src/frame.rs`, `packages/doeff-vm/src/segment.rs`, `packages/doeff-vm/src/continuation.rs`, `packages/doeff-vm/src/value.rs`, `packages/doeff-vm/src/effect.rs`, `packages/doeff-vm/src/do_ctrl.rs`, `packages/doeff-vm/src/yielded.rs` |
| P1-U2 | SPEC-008 handlers and scheduler architecture | `specs/vm/SPEC-008-rust-vm.md` | `1216-2229` | `packages/doeff-vm/src/handler.rs`, `packages/doeff-vm/src/scheduler.rs`, `packages/doeff-vm/src/pyvm.rs`, `packages/doeff-vm/src/do_ctrl.rs`, `packages/doeff-vm/src/dispatch.rs` |
| P1-U3 | SPEC-008 VM state and step machine | `specs/vm/SPEC-008-rust-vm.md` | `2230-3263` | `packages/doeff-vm/src/vm.rs`, `packages/doeff-vm/src/driver.rs`, `packages/doeff-vm/src/step.rs`, `packages/doeff-vm/src/python_call.rs`, `packages/doeff-vm/src/pyvm.rs` |
| P1-U4 | SPEC-008 driver boundary and public contract | `specs/vm/SPEC-008-rust-vm.md` | `3264-3935` | `packages/doeff-vm/src/pyvm.rs`, `doeff/rust_vm.py`, `doeff/__init__.py`, `doeff/_types_internal.py`, `doeff/presets.py`, `doeff/handlers.py` |
| P1-U5 | SPEC-008 control primitives, handlers, memory, invariants | `specs/vm/SPEC-008-rust-vm.md` | `3936-5183` | `packages/doeff-vm/src/vm.rs`, `packages/doeff-vm/src/handler.rs`, `packages/doeff-vm/src/scheduler.rs`, `packages/doeff-vm/src/continuation.rs`, `packages/doeff-vm/src/segment.rs`, `packages/doeff-vm/src/error.rs`, `packages/doeff-vm/src/lib.rs` |
| P1-U6 | SPEC-009 API sections 0-5 (entrypoints, RunResult, Program/@do, effects, handlers) | `specs/vm/SPEC-009-rust-vm-migration.md` | `102-661` | `doeff/rust_vm.py`, `doeff/__init__.py`, `doeff/handlers.py`, `packages/doeff-vm/src/pyvm.rs`, `packages/doeff-vm/src/handler.rs`, `doeff/effects/*.py` |
| P1-U7 | SPEC-009 API sections 6-12 (WithHandler, std handlers, imports, invariants, validation) | `specs/vm/SPEC-009-rust-vm-migration.md` | `662-1101` | `doeff/handlers.py`, `doeff/presets.py`, `doeff/__init__.py`, `doeff/rust_vm.py`, `doeff/_types_internal.py`, `packages/doeff-vm/src/pyvm.rs` |
| P1-U8 | SPEC-TYPES-001 hierarchy and KPC semantics | `specs/core/SPEC-TYPES-001-program-effect-separation.md` | `87-877` | `doeff/program.py`, `doeff/types.py`, `doeff/kleisli.py`, `doeff/do.py`, `packages/doeff-vm/src/pyvm.rs`, `packages/doeff-vm/src/effect.rs`, `packages/doeff-vm/src/handler.rs` |
| P1-U9 | SPEC-TYPES-001 classifier, migration, and public API requirements | `specs/core/SPEC-TYPES-001-program-effect-separation.md` | `878-1433` | `doeff/_types_internal.py`, `doeff/__init__.py`, `doeff/handlers.py`, `doeff/rust_vm.py`, `packages/doeff-vm/src/frame.rs`, `packages/doeff-vm/src/pyvm.rs`, `packages/doeff-vm/src/continuation.rs`, `packages/doeff-vm/src/vm.rs`, `tests/public_api/test_types_001_*.py`, `tests/core/test_sa00*_spec_gaps.py` |
