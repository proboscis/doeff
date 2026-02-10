---
session: SA-002
date: 2026-02-08
specs:
  - specs/vm/SPEC-008-rust-vm.md
  - specs/vm/SPEC-009-rust-vm-migration.md
  - specs/core/SPEC-TYPES-001-program-effect-separation.md
defaults:
  C: fix-spec
  G: fix-code
  Q: discuss
---

# SA-002 Resolutions

Valid values: `fix-code`, `fix-spec`, `fix-both`, `defer`, `add-to-spec`, `remove-from-code`, `discuss`, `skip`

| ID | Resolution | Notes |
|---|---|---|
| SA-002-C01 | fix-spec | Enforce mandatory KPC effect-dispatch path; remove conflicting call-upgrade wording |
| SA-002-G01 | fix-code |  |
| SA-002-G02 | fix-code |  |
| SA-002-G03 | fix-code |  |
| SA-002-G04 | fix-code |  |
| SA-002-G05 | fix-code |  |
| SA-002-G06 | fix-code |  |
| SA-002-G07 | fix-code |  |
| SA-002-G08 | fix-code |  |
| SA-002-G09 | fix-code |  |
| SA-002-Q01 | remove-from-code | Fallback behavior should not exist unless explicitly specified in spec |
| SA-002-Q02 | fix-both | Clarify spec that `doeff_vm` is internal/non-user API and tighten code/docs boundaries accordingly |
| SA-002-Q03 | fix-both | Remove transitional hybrid state; require Rust-side KPC/primitive implementation only |
