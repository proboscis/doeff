# SA-006 Spec Audit Report (Round 2)

- Session: `SA-006`
- Date: `2026-02-08`
- Trigger: Re-review after `SPEC-TYPES-001` Rev10 and implementation updates
- Specs audited:
  - `specs/vm/SPEC-008-rust-vm.md`
  - `specs/vm/SPEC-009-rust-vm-migration.md`
  - `specs/core/SPEC-TYPES-001-program-effect-separation.md`

## Summary

- Total actionable findings: 8
- Contradictions (C): 0
- Gaps (G): 8
  - Critical: 4
  - Moderate: 3
  - Minor: 1
- Discussion (Q): 0

## Gaps

### Critical

| ID | Description | Spec refs | Impl refs | Enforcement | Resolution |
|---|---|---|---|---|---|
| SA-006-G01 | Entry boundary still performs Python-side normalization and duck typing; SPEC-009 requires strict DoExpr boundary semantics. | `specs/vm/SPEC-009-rust-vm-migration.md:204`, `specs/vm/SPEC-009-rust-vm-migration.md:244`, `specs/vm/SPEC-009-rust-vm-migration.md:1000` | `doeff/rust_vm.py:13`, `doeff/rust_vm.py:22`, `doeff/rust_vm.py:56`, `packages/doeff-vm/src/pyvm.rs:506` | test | auto-fix-code |
| SA-006-G02 | Validation matrix/API-16/17 boundary checks are incomplete (handlers/env/store and error quality contract). | `specs/vm/SPEC-009-rust-vm-migration.md:983`, `specs/vm/SPEC-009-rust-vm-migration.md:1001`, `specs/vm/SPEC-009-rust-vm-migration.md:1011` | `doeff/rust_vm.py:31`, `doeff/rust_vm.py:57`, `packages/doeff-vm/src/pyvm.rs:1617` | test | auto-fix-code |
| SA-006-G03 | Dispatch primitive constructors (`Resume/Transfer/Delegate/WithHandler`) are not enforcing construction-time validation as required. | `specs/vm/SPEC-009-rust-vm-migration.md:1041`, `specs/vm/SPEC-009-rust-vm-migration.md:1058` | `packages/doeff-vm/src/pyvm.rs:948`, `packages/doeff-vm/src/pyvm.rs:965`, `packages/doeff-vm/src/pyvm.rs:987`, `packages/doeff-vm/src/pyvm.rs:1004` | test + semgrep | auto-fix-code |
| SA-006-G04 | Rev10 binary hierarchy contract not implemented: `Program = DoExpr` and no `DoThunk` path. | `specs/core/SPEC-TYPES-001-program-effect-separation.md:255`, `specs/core/SPEC-TYPES-001-program-effect-separation.md:270`, `specs/core/SPEC-TYPES-001-program-effect-separation.md:341` | `doeff/program.py:255`, `doeff/program.py:565` | test + semgrep | auto-fix-code |

### Moderate

| ID | Description | Spec refs | Impl refs | Enforcement | Resolution |
|---|---|---|---|---|---|
| SA-006-G05 | Rev10 composability model is not implemented as DoCtrl AST nodes (`Map/FlatMap/Pure`) and still relies on generator wrappers. | `specs/core/SPEC-TYPES-001-program-effect-separation.md:259`, `specs/core/SPEC-TYPES-001-program-effect-separation.md:643`, `specs/core/SPEC-TYPES-001-program-effect-separation.md:668` | `doeff/program.py:332`, `doeff/program.py:344`, `doeff/program.py:466`, `doeff/program.py:516` | test | auto-fix-code |
| SA-006-G06 | Callstack public surface still centers `ProgramCallStackEffect` while Rev10 taxonomy requires `GetCallStack` DoCtrl path as canonical. | `specs/core/SPEC-TYPES-001-program-effect-separation.md:815`, `specs/core/SPEC-TYPES-001-program-effect-separation.md:894` | `doeff/effects/callstack.py:34`, `doeff/effects/__init__.py:27`, `doeff/__init__.py:35` | test | auto-fix-code |
| SA-006-G07 | Handler identity preservation remains broken for Rust sentinel handlers (`GetHandlers` path returns placeholders). | `specs/vm/SPEC-008-rust-vm.md:1219`, `specs/vm/SPEC-008-rust-vm.md:1221` | `packages/doeff-vm/src/vm.rs:1146`, `packages/doeff-vm/src/value.rs:50` | test | auto-fix-code |

### Minor

| ID | Description | Spec refs | Impl refs | Enforcement | Resolution |
|---|---|---|---|---|---|
| SA-006-G08 | SPEC-008 U1 contract still expects `WithHandler(..., expr)` naming while implementation exposes `.program`; interface text drift remains. | `specs/vm/SPEC-008-rust-vm.md:1036`, `specs/vm/SPEC-008-rust-vm.md:1041` | `packages/doeff-vm/src/pyvm.rs:938`, `packages/doeff-vm/src/pyvm.rs:950` | test | auto-fix-code |

## Blocking Dependency Resolution Ledger (Mandatory)

- fact: `run/async_run` still normalize program inputs and accept duck-typed generators -> issue: SA-006-G01 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: boundary validation matrix requirements are not fully enforced with spec-required error messaging -> issue: SA-006-G02 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G01]
- fact: primitive constructors defer validation until classify/dispatch path -> issue: SA-006-G03 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G01]
- fact: Rev10 requires binary hierarchy and `Program = DoExpr` while implementation keeps `DoThunk` and `ProgramBase` alias -> issue: SA-006-G04 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: composition remains generator-based rather than DoCtrl AST node composition -> issue: SA-006-G05 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G04]
- fact: callstack API still exposed as legacy effect path instead of canonical `GetCallStack` DoCtrl route -> issue: SA-006-G06 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G04]
- fact: `GetHandlers` identity preservation remains broken for Rust sentinels -> issue: SA-006-G07 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: `WithHandler` field naming contract in spec vs implementation remains mismatched -> issue: SA-006-G08 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
