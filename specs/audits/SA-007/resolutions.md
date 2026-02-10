---
session: SA-007
date: 2026-02-08
specs:
  - specs/vm/SPEC-008-rust-vm.md
  - specs/vm/SPEC-009-rust-vm-migration.md
  - specs/core/SPEC-TYPES-001-program-effect-separation.md
defaults:
  C: discuss
  G: fix-code
  Q: discuss
---

| ID | Fact refs | Classification basis | Auto/default | Resolution | Depends on | Notes |
|---|---|---|---|---|---|---|
| SA-007-C01 | `SPEC-008:303-304`, `SPEC-008:1186-1188`, `SPEC-008:195-198` | spec-spec naming conflict | discuss | fix-spec | [] | Canonical field: `expr: DoExpr` (not `program`) |
| SA-007-C02 | `SPEC-TYPES-001:1337`, `SPEC-TYPES-001:1372` | spec-spec behavior conflict | discuss | fix-spec | [] | `default_handlers` MUST include KPC |
| SA-007-C03 | `SPEC-008:1095-1096`, `SPEC-008:1117-1164` | spec-spec architecture conflict | discuss | fix-spec | [] | No fallback: tag check without GIL using DoExpr tag |
| SA-007-G01 | `SPEC-009:240-245`, `rust_vm.py:13`, `rust_vm.py:22` | boundary behavior mismatch | auto-fix-code | fix-code | [SA-007-C01] | |
| SA-007-G02 | `SPEC-009:203-219`, `SPEC-009:983-1011`, `rust_vm.py:23`, `pyvm.rs:507` | validation/contract mismatch | auto-fix-code | fix-code | [SA-007-C01] | |
| SA-007-G03 | `SPEC-009:1041-1058`, `pyvm.rs:947`, `pyvm.rs:964`, `pyvm.rs:1003` | constructor validation missing | auto-fix-code | fix-code | [SA-007-G01,SA-007-G02] | |
| SA-007-G04 | `SPEC-TYPES-001:303-370`, `SPEC-TYPES-001:878-921`, `program.py:255`, `pyvm.rs:507` | architecture drift | auto-fix-code | fix-code | [SA-007-C02] | |
| SA-007-G05 | `SPEC-TYPES-001:878-921`, `SPEC-008:3995-4040`, `do_ctrl.rs:13`, `program.py:342` | missing control-node model | auto-fix-code | fix-code | [SA-007-G04] | |
| SA-007-G06 | `SPEC-009:204-244`, `pyvm.rs:507`, `rust_vm.py:23` | top-level DoCtrl acceptance mismatch | auto-fix-code | fix-code | [SA-007-G01,SA-007-G05] | |
| SA-007-G07 | `SPEC-008:1219-1221`, `value.rs:50`, `continuation.rs:48` | identity preservation mismatch | auto-fix-code | fix-code | [] | |
| SA-007-G08 | `SPEC-008:1117-1164`, `SPEC-TYPES-001:922-1012`, `pyvm.rs:566` | classifier architecture drift | auto-fix-code | fix-code | [SA-007-C03,SA-007-G04] | |
| SA-007-Q01 | `pyvm.rs:189`, `pyvm.rs:271`, `pyvm.rs:305` | spec silent on extra public ops | discuss | remove-from-code | [] | Hide non-public API from public surface |
| SA-007-Q02 | `handlers.py:7`, `__init__.py:198` | spec silent on kpc export surface | discuss | add-to-spec | [] | Canonical surface: `kpc` is imported from `doeff.handlers` |
| SA-007-Q03 | `arena.rs:6`, `vm.rs:152` | spec silence on internal memory strategy | discuss | add-to-spec | [] | Arena/free-list is implementation detail; non-normative in spec |
