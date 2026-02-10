# SA-008 Phase 1 Tasks (Subagent Variant)

Session: `SA-008`

| Task ID | Section | Spec file | Line range | Target implementation files |
|---|---|---|---|---|
| P1-U1 | SPEC-008 summary/design/core principles | `specs/vm/SPEC-008-rust-vm.md` | `95-407` | `doeff/rust_vm.py`, `doeff/__init__.py`, `doeff/handlers.py`, `doeff/presets.py` |
| P1-U2 | SPEC-008 rust data structures | `specs/vm/SPEC-008-rust-vm.md` | `408-2230` | `packages/doeff-vm/src/{ids.rs,frame.rs,segment.rs,continuation.rs,value.rs,effect.rs,do_ctrl.rs,yielded.rs,dispatch.rs,handler.rs,scheduler.rs}` |
| P1-U3 | SPEC-008 VM state + step machine | `specs/vm/SPEC-008-rust-vm.md` | `2231-3268` | `packages/doeff-vm/src/{vm.rs,step.rs,driver.rs,python_call.rs,pyvm.rs}` |
| P1-U4 | SPEC-008 driver/public contract/controls | `specs/vm/SPEC-008-rust-vm.md` | `3269-4837` | `packages/doeff-vm/src/{pyvm.rs,vm.rs,do_ctrl.rs,handler.rs}`, `doeff/rust_vm.py`, `doeff/__init__.py` |
| P1-U5 | SPEC-008 memory/invariants/legacy+crate | `specs/vm/SPEC-008-rust-vm.md` | `4838-5196` | `packages/doeff-vm/src/{arena.rs,vm.rs,continuation.rs,lib.rs}` |
| P1-U6 | SPEC-009 sections 0-5 | `specs/vm/SPEC-009-rust-vm-migration.md` | `102-661` | `doeff/{rust_vm.py,__init__.py,run.py,program.py,do.py,handlers.py}`, `packages/doeff-vm/src/{pyvm.rs,handler.rs}` |
| P1-U7 | SPEC-009 sections 6-12 | `specs/vm/SPEC-009-rust-vm-migration.md` | `662-1109` | `doeff/{handlers.py,presets.py,__init__.py,rust_vm.py}`, `packages/doeff-vm/src/{pyvm.rs,handler.rs,scheduler.rs}` |
| P1-U8 | TYPES-001 principles/hierarchy/KPC/@do | `specs/core/SPEC-TYPES-001-program-effect-separation.md` | `87-877` | `doeff/{program.py,_types_internal.py,types.py,kleisli.py,do.py}`, `packages/doeff-vm/src/{pyvm.rs,effect.rs,handler.rs,do_ctrl.rs}` |
| P1-U9 | TYPES-001 taxonomy/classifier/migration/tests | `specs/core/SPEC-TYPES-001-program-effect-separation.md` | `878-1445` | `doeff/{program.py,_types_internal.py,__init__.py,handlers.py,rust_vm.py}`, `packages/doeff-vm/src/{pyvm.rs,vm.rs,do_ctrl.rs,continuation.rs}`, `tests/public_api/test_types_001_*.py`, `tests/core/test_sa00*_spec_gaps.py` |
