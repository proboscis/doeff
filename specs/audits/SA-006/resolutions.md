---
session: SA-006
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

| ID | Fact refs | Classification basis | Auto/default | Resolution | Depends on | Notes |
|---|---|---|---|---|---|---|
| SA-006-C01 | `SPEC-008:12`, `SPEC-008:1610` | spec-spec conflict | discuss |  | [] | |
| SA-006-C02 | `TYPES-001:614`, `TYPES-001:668` | spec-spec conflict | discuss |  | [] | |
| SA-006-G01 | `SPEC-008:826-859`, `effect.rs:16` | behavior/shape mismatch | auto-fix-code | fix-code | [SA-006-C01] | |
| SA-006-G02 | `SPEC-008:1019-1059`, `continuation.rs:130` | contract mismatch | auto-fix-code | fix-code | [] | |
| SA-006-G03 | `SPEC-008:293,1070`, `value.rs:50` | identity-preservation mismatch | auto-fix-code | fix-code | [SA-006-G02] | |
| SA-006-G04 | `SPEC-009:239-244`, `rust_vm.py:13` | boundary-behavior mismatch | auto-fix-code | fix-code | [] | |
| SA-006-G05 | `SPEC-009:204-213`, `rust_vm.py:23` | input contract mismatch | auto-fix-code | fix-code | [SA-006-G04] | |
| SA-006-G06 | `SPEC-009:997-1058`, `rust_vm.py:57`, `pyvm.rs:940` | missing validation/constructor checks | auto-fix-code | fix-code | [SA-006-G04] | |
| SA-006-G07 | `TYPES-001:111,116`, `program.py:248` | hierarchy alias mismatch | auto-fix-code | fix-code | [SA-006-C02] | |
| SA-006-G08 | `TYPES-001:111,171`, `program.py:332` | composability contract mismatch | auto-fix-code | fix-code | [SA-006-G07] | |
| SA-006-G09 | `TYPES-001:637-652`, `callstack.py:34` | bridge contract mismatch | auto-fix-code | fix-code | [SA-006-C02] | |
| SA-006-G10 | `TYPES-001:299`, `program.py:64` | annotation normalization drift | auto-fix-code | fix-code | [SA-006-G07] | |
| SA-006-Q01 | `scheduler.rs:130,419` | spec silent extra behavior | discuss |  | [] | |
| SA-006-Q02 | `handlers.py:7`, `rust_vm.py:36` | spec silent public surface choice | discuss |  | [] | |
| SA-006-Q03 | `test_sa002_spec_gaps.py:1`, `test_sa003_spec_gaps.py:1` | spec silent test placement/policy | discuss |  | [] | |
