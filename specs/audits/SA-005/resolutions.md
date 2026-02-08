---
session: SA-005
date: 2026-02-08
specs:
  - specs/vm-architecture/SPEC-008-rust-vm.md
  - specs/vm-architecture/SPEC-009-rust-vm-migration.md
  - specs/SPEC-TYPES-001-program-effect-separation.md
defaults:
  C: discuss
  G: fix-code
  Q: discuss
---

Valid resolutions: `fix-code`, `fix-spec`, `fix-both`, `add-to-spec`, `remove-from-code`, `discuss`, `defer`, `skip`.

| ID | Fact refs | Classification basis | Auto/default | Resolution | Depends on | Notes |
|---|---|---|---|---|---|---|
| SA-005-C01 | `SPEC-008:12`, `SPEC-008:1610` | spec-spec conflict | discuss |  | [] | |
| SA-005-G01 | `SPEC-008:823`, `effect.rs:16` | behavior/shape mismatch | auto-fix-code | fix-code | [SA-005-C01] | |
| SA-005-G02 | `SPEC-008:864`, `effect.rs:16`, `scheduler.rs:145` | missing typed scheduler effects | auto-fix-code | fix-code | [SA-005-C01] | |
| SA-005-G03 | `SPEC-008:1127`, `handler.rs:42` | contract drift with possible spec evolution | discussion-required |  | [SA-005-C01] | confirm whether spec or code is normative |
| SA-005-G04 | `SPEC-009:238`, `rust_vm.py:13` | behavioral contract mismatch | auto-fix-code | fix-code | [] | |
| SA-005-G05 | `SPEC-009:203`, `pyvm.rs:508` | input/error contract mismatch | auto-fix-code | fix-code | [SA-005-G04] | |
| SA-005-G06 | `SPEC-009:746`, `handlers.py:7` | missing API export | auto-fix-code | fix-code | [SA-005-Q01] | |
| SA-005-G07 | `SPEC-TYPES-001:111`, `program.py:248` | architecture/hierarchy drift | discussion-required |  | [SA-005-C01] | high-impact type model decision |
| SA-005-G08 | `SPEC-TYPES-001:744`, `pyvm.rs:548` | structural classifier-order drift | auto-fix-code | fix-code | [SA-005-G07] | |
| SA-005-Q01 | `presets.py:15`, `rust_vm.py:35` | spec silent/ambiguous composition | discuss |  | [] | |
| SA-005-Q02 | `pyvm.rs:568`, `continuation.rs:130` | spec-silent interop representation split | discuss |  | [SA-005-C01] | |
| SA-005-Q03 | `callstack.py:34`, `pyvm.rs:653` | spec-silent bridge choice | discuss |  | [SA-005-G07] | |
