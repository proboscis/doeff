---
session: SA-003
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

# SA-003 Resolutions

Valid values: `fix-code`, `fix-spec`, `fix-both`, `defer`, `add-to-spec`, `remove-from-code`, `discuss`, `skip`

| ID | Resolution | Notes |
|---|---|---|
| SA-003-C01 | fix-spec | Clarify hybrid vs strict-Rust base ownership and deconflict SPEC-008/TYPES-001 wording |
| SA-003-G01 | fix-code | Add dedicated pending state for CallFunc return path |
| SA-003-G02 | fix-code | Normalize StartProgram/CallHandler/CallAsync failures to `PyCallOutcome::GenError` |
| SA-003-G03 | fix-code | Remove isolated->shared fallback; throw runtime error |
| SA-003-G04 | fix-code | Make `GetHandlers` fail on missing handler entries |
| SA-003-G05 | fix-spec | Keep current direct stdlib install path documented as compatibility layer |
| SA-003-G06 | fix-both | Add `PythonAsyncSyntaxEscape` alias in code and align spec naming |
| SA-003-G07 | fix-spec | Update crate structure section to match actual module layout |
| SA-003-G08 | fix-spec | Adjust GIL-boundary invariant wording to match intended behavior |
| SA-003-Q01 | skip | Already specified: PyVM is implementation-layer; only run/async_run are public entrypoints |
| SA-003-Q02 | skip | Already specified: run accepts DoExpr/Program contract; reject non-DoExpr objects |
| SA-003-Q03 | skip | Callback mechanism is internal-only and not part of user API surface |
| SA-003-Q04 | fix-code | Enforce strict classify path: no getattr/import/string-based metadata extraction |
