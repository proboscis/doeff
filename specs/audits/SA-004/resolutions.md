---
session: SA-004
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

# SA-004 Resolutions

Valid values: `fix-code`, `fix-spec`, `fix-both`, `defer`, `add-to-spec`, `remove-from-code`, `discuss`, `skip`

| ID | Resolution | Notes |
|---|---|---|
| SA-004-G01 | TBD | SPEC-008 fielded effect pyclasses vs current marker-based extraction path |
| SA-004-G02 | TBD | SPEC-008 typed scheduler pyclasses vs marker-based parsing |
| SA-004-G03 | TBD | Handler trait signature drift (`Bound<PyAny>` docs vs `DispatchEffect` impl) |
| SA-004-G04 | TBD | `sync_preset` scheduler inclusion text vs actual preset behavior |
| SA-004-G05 | TBD | Entrypoint signature strictness (`list[Handler]` vs `Sequence[Any]`) |
| SA-004-G06 | TBD | `Modify(key, fn)` naming drift (`fn` vs `f`/`func`) |
| SA-004-G07 | TBD | `DerivedProgram` naming drift (`GeneratorProgram` in impl) |
| SA-004-G08 | TBD | Test-only `auto_unwrap_strategy` attr read in KPC parser path |
| SA-004-Q01 | TBD | Clarify `RunResult.result` wrapper representation |
| SA-004-Q02 | TBD | `PureProgram` vs `PureEffect` semantic direction |
| SA-004-Q03 | TBD | Continuation `K` representation (`PyK` vs dict conversion path) |
| SA-004-Q04 | TBD | Explicit KPC handler policy (carry-over decision) |
