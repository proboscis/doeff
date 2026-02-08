# SA-006 Phase 1 Tasks

Team: `spec-audit-SA-006`
Session: `SA-006`

| Task ID | Section | Spec file | Line range | Target implementation files |
|---|---|---|---|---|
| P1-U1 | SPEC-008 core architecture and data structures | `specs/vm-architecture/SPEC-008-rust-vm.md` | `117-1215` | `packages/doeff-vm/src/ids.rs`, `packages/doeff-vm/src/py_shared.rs`, `packages/doeff-vm/src/frame.rs`, `packages/doeff-vm/src/segment.rs`, `packages/doeff-vm/src/continuation.rs`, `packages/doeff-vm/src/value.rs`, `packages/doeff-vm/src/effect.rs`, `packages/doeff-vm/src/do_ctrl.rs`, `packages/doeff-vm/src/yielded.rs` |
| P1-U2 | SPEC-008 handlers and scheduler | `specs/vm-architecture/SPEC-008-rust-vm.md` | `1216-2023` | `packages/doeff-vm/src/handler.rs`, `packages/doeff-vm/src/scheduler.rs`, `packages/doeff-vm/src/pyvm.rs`, `packages/doeff-vm/src/do_ctrl.rs` |
| P1-U3 | SPEC-008 VM state and step machine | `specs/vm-architecture/SPEC-008-rust-vm.md` | `2024-2972` | `packages/doeff-vm/src/vm.rs`, `packages/doeff-vm/src/pyvm.rs`, `packages/doeff-vm/src/driver.rs` |
| P1-U4 | SPEC-008 public contract and runtime boundary | `specs/vm-architecture/SPEC-008-rust-vm.md` | `2973-3594` | `packages/doeff-vm/src/pyvm.rs`, `doeff/rust_vm.py`, `doeff/__init__.py`, `doeff/_types_internal.py`, `doeff/presets.py`, `doeff/handlers.py` |
| P1-U5 | SPEC-008 control primitives and invariants | `specs/vm-architecture/SPEC-008-rust-vm.md` | `3595-4677` | `packages/doeff-vm/src/vm.rs`, `packages/doeff-vm/src/handler.rs`, `packages/doeff-vm/src/scheduler.rs`, `packages/doeff-vm/src/continuation.rs`, `packages/doeff-vm/src/segment.rs` |
| P1-U6 | SPEC-009 entrypoints/public API and validation | `specs/vm-architecture/SPEC-009-rust-vm-migration.md` | `143-1100` | `doeff/rust_vm.py`, `doeff/__init__.py`, `doeff/handlers.py`, `doeff/presets.py`, `packages/doeff-vm/src/pyvm.rs`, `doeff/effects/*.py` |
| P1-U7 | SPEC-TYPES-001 hierarchy and KPC semantics | `specs/SPEC-TYPES-001-program-effect-separation.md` | `55-531` | `doeff/program.py`, `doeff/types.py`, `doeff/kleisli.py`, `doeff/do.py`, `packages/doeff-vm/src/pyvm.rs`, `packages/doeff-vm/src/effect.rs`, `packages/doeff-vm/src/handler.rs` |
| P1-U8 | SPEC-TYPES-001 callstack/classifier/migration/tests | `specs/SPEC-TYPES-001-program-effect-separation.md` | `532-1189` | `doeff/effects/callstack.py`, `doeff/analysis.py`, `doeff/__main__.py`, `packages/doeff-vm/src/frame.rs`, `packages/doeff-vm/src/pyvm.rs`, `packages/doeff-vm/src/continuation.rs`, `packages/doeff-vm/src/vm.rs`, `tests/core/test_sa002_spec_gaps.py`, `tests/core/test_sa003_spec_gaps.py` |
