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
| SA-006-G01 | `SPEC-009:204-244`, `rust_vm.py:13`, `pyvm.rs:506` | boundary behavior mismatch | auto-fix-code | fix-code | [] | |
| SA-006-G02 | `SPEC-009:983-1011`, `rust_vm.py:31` | validation matrix mismatch | auto-fix-code | fix-code | [SA-006-G01] | |
| SA-006-G03 | `SPEC-009:1041-1058`, `pyvm.rs:948` | constructor-time validation missing | auto-fix-code | fix-code | [SA-006-G01] | |
| SA-006-G04 | `TYPES-001:255,270,341`, `program.py:255,565` | hierarchy mismatch (Rev10) | auto-fix-code | fix-code | [] | |
| SA-006-G05 | `TYPES-001:259,643,668`, `program.py:332,466` | composition model mismatch | auto-fix-code | fix-code | [SA-006-G04] | |
| SA-006-G06 | `TYPES-001:815,894`, `callstack.py:34` | callstack route mismatch | auto-fix-code | fix-code | [SA-006-G04] | |
| SA-006-G07 | `SPEC-008:1219-1221`, `vm.rs:1146`, `value.rs:50` | identity preservation mismatch | auto-fix-code | fix-code | [] | |
| SA-006-G08 | `SPEC-008:1036-1041`, `pyvm.rs:938` | naming/API drift | auto-fix-code | fix-code | [] | |
