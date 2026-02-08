---
session: SA-008
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
| SA-008-C01 | `SPEC-TYPES-001:7-9`, `SPEC-TYPES-001:1299-1310` | spec-spec conflict | discuss | fix-spec | [] | Spec is source of truth: remove all DoThunk compatibility expectations |
| SA-008-C02 | `SPEC-TYPES-001:922-947`, `SPEC-008:1117-1134` | spec-spec conflict | discuss | fix-spec | [] | Strict classifier only; no fallback path |
| SA-008-G01 | `SPEC-TYPES-001:928-947`, `yielded.rs:9` | behavior mismatch | auto-fix-code | fix-code | [SA-008-C02] | |
| SA-008-G02 | `SPEC-TYPES-001:922-1012`, `pyvm.rs:568` | architecture mismatch | auto-fix-code | fix-code | [SA-008-C02,SA-008-G01] | |
| SA-008-G03 | `SPEC-TYPES-001:1008`, `program.py:556` | migration gap | auto-fix-code | fix-code | [SA-008-C01] | |
| SA-008-G04 | `SPEC-TYPES-001:878-921`, `program.py:324` | composition model gap | auto-fix-code | fix-code | [SA-008-C01,SA-008-G03] | |
| SA-008-G05 | `SPEC-008:4028-4046`, `vm.rs:517` | runtime semantics missing | auto-fix-code | fix-code | [SA-008-G04] | |
| SA-008-G06 | `SPEC-008:807-892`, `handler.rs:110` | structural classifier mismatch | auto-fix-code | fix-code | [SA-008-C02] | |
| SA-008-G07 | `SPEC-008:864-907`, `scheduler.rs:145` | structural classifier mismatch | auto-fix-code | fix-code | [SA-008-C02,SA-008-G06] | |
| SA-008-G08 | `SPEC-TYPES-001:371-550`, `handler.rs:182`, `program.py:214` | architecture split mismatch | auto-fix-code | fix-code | [SA-008-G02,SA-008-G04] | |
| SA-008-G09 | `SPEC-009:260-330`, `pyvm.rs:852`, `_types_internal.py:868` | API behavior mismatch | auto-fix-code | fix-code | [] | |
| SA-008-G10 | `SPEC-009:143-259`, `vm.rs:1005` | API error-surface mismatch | auto-fix-code | fix-code | [] | |
| SA-008-G11 | `SPEC-009:840-882`, `doeff_vm/__init__.py:7` | exposure policy mismatch | auto-fix-code | fix-code | [] | |
| SA-008-Q01 | `program.py:556`, `test_types_001_hierarchy.py:12` | compatibility policy question | discuss | remove-from-code | [SA-008-C01] | No compatibility alias/transition mode |
| SA-008-Q02 | `yielded.rs:9`, `pyvm.rs:685` | runtime-safety policy question | discuss | fix-both | [SA-008-C02] | No fallback; fail hard with clear Python exception where possible |
| SA-008-Q03 | `run.py:447`, `rust_vm.py:48` | UX policy question | discuss | fix-both | [] | All public APIs strict typed boundaries; no duck typing/wrapping |
