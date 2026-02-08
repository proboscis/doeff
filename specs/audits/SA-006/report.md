# SA-006 Spec Audit Report

- Session: `SA-006`
- Date: `2026-02-08`
- Specs audited:
  - `specs/vm-architecture/SPEC-008-rust-vm.md`
  - `specs/vm-architecture/SPEC-009-rust-vm-migration.md`
  - `specs/SPEC-TYPES-001-program-effect-separation.md`
- Implementation directory:
  - `doeff/`
  - `packages/doeff-vm/`

## Summary

- Total findings: 15
- Contradictions (C): 2
- Gaps (G): 10
  - Critical: 6
  - Moderate: 3
  - Minor: 1
- Discussion (Q): 3

```
┌───────────────────────────────────────────────────────────────┐
│                    SPEC-vs-IMPL GAPS (SA-006)                │
├───────────────────────────────────────────────────────────────┤
│ CONTRADICTIONS                                                │
│  - SA-006-C01  Effect enum removed vs legacy enum snippets   │
│  - SA-006-C02  KPC metadata extraction locus conflict         │
│                                                               │
│ CRITICAL GAPS                                                 │
│  - SA-006-G01  Effect pyclass fielded contract mismatch       │
│  - SA-006-G02  Continuation handle not opaque                 │
│  - SA-006-G03  Handler identity not preserved on conversion   │
│  - SA-006-G04  run()/async_run() Python-side normalization    │
│  - SA-006-G05  run() boundary rejects required DoCtrl inputs  │
│  - SA-006-G06  API boundary validation matrix not enforced    │
│                                                               │
│ MODERATE GAPS                                                 │
│  - SA-006-G07  Program alias/hierarchy drift (Program=DoExpr) │
│  - SA-006-G08  DoExpr composability contract not implemented  │
│  - SA-006-G09  callstack bridge uses legacy effect path       │
│                                                               │
│ MINOR GAPS                                                    │
│  - SA-006-G10  string annotation 'Thunk' normalization drift  │
│                                                               │
│ DISCUSSION                                                    │
│  - SA-006-Q01  ready_waiters queue undocumented in spec text  │
│  - SA-006-Q02  extra public handler sentinels scope policy    │
│  - SA-006-Q03  public_api test location/spec mapping drift    │
└───────────────────────────────────────────────────────────────┘
```

## Contradictions

| ID | Description | Spec refs | Impl chose | Resolution |
|---|---|---|---|---|
| SA-006-C01 | SPEC-008 states Effect enum removed, but legacy enum-style references remain in crate/invariant sections. | `specs/vm-architecture/SPEC-008-rust-vm.md:12`, `specs/vm-architecture/SPEC-008-rust-vm.md:1610` | Current code uses opaque `DispatchEffect`/Python effect object flow. | discuss |
| SA-006-C02 | TYPES-001 indicates KPC metadata extraction in classifier and in KPC handler (conflicting locus). | `specs/SPEC-TYPES-001-program-effect-separation.md:614`, `specs/SPEC-TYPES-001-program-effect-separation.md:668` | Current code extracts in KPC handler; classifier stays base-type only. | discuss |

## Gaps

### Critical

| ID | Description | Spec refs | Impl refs | Enforcement | Resolution |
|---|---|---|---|---|---|
| SA-006-G01 | Fielded effect pyclass contract diverges (`Get/Put/Modify/Ask/Tell` payload shape/class naming). | `specs/vm-architecture/SPEC-008-rust-vm.md:826`, `specs/vm-architecture/SPEC-008-rust-vm.md:859` | `packages/doeff-vm/src/effect.rs:16`, `packages/doeff-vm/src/effect.rs:49` | test + semgrep | auto-fix-code |
| SA-006-G02 | Continuation Python conversion returns inspectable dict instead of opaque K handle. | `specs/vm-architecture/SPEC-008-rust-vm.md:1019`, `specs/vm-architecture/SPEC-008-rust-vm.md:1059` | `packages/doeff-vm/src/continuation.rs:130` | test | auto-fix-code |
| SA-006-G03 | Handler identity preservation contract breaks when Rust handlers serialize as placeholder string. | `specs/vm-architecture/SPEC-008-rust-vm.md:293`, `specs/vm-architecture/SPEC-008-rust-vm.md:1070` | `packages/doeff-vm/src/value.rs:50`, `packages/doeff-vm/src/continuation.rs:144` | test + semgrep | auto-fix-code |
| SA-006-G04 | SPEC-009 forbids Python-side entry normalization; wrapper still normalizes before VM boundary. | `specs/vm-architecture/SPEC-009-rust-vm-migration.md:239`, `specs/vm-architecture/SPEC-009-rust-vm-migration.md:959` | `doeff/rust_vm.py:13`, `doeff/rust_vm.py:56` | test | auto-fix-code |
| SA-006-G05 | `run()` input boundary diverges from DoExpr acceptance contract (notably DoCtrl path). | `specs/vm-architecture/SPEC-009-rust-vm-migration.md:204`, `specs/vm-architecture/SPEC-009-rust-vm-migration.md:213` | `doeff/rust_vm.py:23`, `doeff/rust_vm.py:31` | test | auto-fix-code |
| SA-006-G06 | Validation matrix/API-16/17 constraints not enforced at boundary; constructor-time validation missing for dispatch primitives. | `specs/vm-architecture/SPEC-009-rust-vm-migration.md:997`, `specs/vm-architecture/SPEC-009-rust-vm-migration.md:1058` | `doeff/rust_vm.py:57`, `packages/doeff-vm/src/pyvm.rs:940` | test + semgrep | auto-fix-code |

### Moderate

| ID | Description | Spec refs | Impl refs | Enforcement | Resolution |
|---|---|---|---|---|---|
| SA-006-G07 | Program alias drift: spec says `Program = DoExpr`, implementation keeps `Program = ProgramBase`. | `specs/SPEC-TYPES-001-program-effect-separation.md:111`, `specs/SPEC-TYPES-001-program-effect-separation.md:116` | `doeff/program.py:248`, `doeff/program.py:565` | test + semgrep | auto-fix-code |
| SA-006-G08 | DoExpr composability contract (`map/flat_map/pure`) is not implemented on DoExpr root as specified. | `specs/SPEC-TYPES-001-program-effect-separation.md:111`, `specs/SPEC-TYPES-001-program-effect-separation.md:171` | `doeff/program.py:248`, `doeff/program.py:332` | test | auto-fix-code |
| SA-006-G09 | Callstack retrieval bridge still uses legacy effect surface instead of unified `GetCallStack` DoCtrl path. | `specs/SPEC-TYPES-001-program-effect-separation.md:637`, `specs/SPEC-TYPES-001-program-effect-separation.md:652` | `doeff/effects/callstack.py:34`, `packages/doeff-vm/src/vm.rs:556` | test | auto-fix-code |

### Minor

| ID | Description | Spec refs | Impl refs | Enforcement | Resolution |
|---|---|---|---|---|---|
| SA-006-G10 | String-annotation normalization drift (`Thunk` alias absent where spec examples include it). | `specs/SPEC-TYPES-001-program-effect-separation.md:299` | `doeff/program.py:64`, `doeff/program.py:98` | test | auto-fix-code |

## Discussion

| ID | Description | Impl refs | Resolution |
|---|---|---|---|
| SA-006-Q01 | Scheduler `ready_waiters` queue is an additive runtime detail not explicitly covered by current spec slice. | `packages/doeff-vm/src/scheduler.rs:130`, `packages/doeff-vm/src/scheduler.rs:419` | discuss |
| SA-006-Q02 | Public handler surface includes `kpc` and `scheduler`; scope policy should be explicit relative to contract wording. | `doeff/handlers.py:7`, `doeff/rust_vm.py:36` | discuss |
| SA-006-Q03 | Public API test requirement references and current gap tests are structurally focused; policy on canonical public test location unresolved. | `tests/core/test_sa002_spec_gaps.py:1`, `tests/core/test_sa003_spec_gaps.py:1` | discuss |

## Blocking Dependency Resolution Ledger (Mandatory)

- fact: SPEC-008 says Effect enum removed while later text retains enum-style references -> issue: SA-006-C01 -> auto-resolve/discussion: discussion-required -> action: discuss -> dependencies: []
- fact: TYPES-001 presents conflicting KPC metadata extraction loci (classifier vs handler) -> issue: SA-006-C02 -> auto-resolve/discussion: discussion-required -> action: discuss -> dependencies: []
- fact: effect pyclass payload/class contract diverges from fielded spec contract -> issue: SA-006-G01 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-C01]
- fact: continuation conversion exposes internals instead of opaque K -> issue: SA-006-G02 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: handler identity preservation breaks via `rust_program_handler` placeholder -> issue: SA-006-G03 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G02]
- fact: Python wrapper performs program normalization prohibited by SPEC-009 boundary contract -> issue: SA-006-G04 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: DoCtrl acceptance path and boundary error messaging diverge from spec contract -> issue: SA-006-G05 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G04]
- fact: validation matrix requirements for boundary checks/constructor checks are not enforced -> issue: SA-006-G06 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G04]
- fact: Program alias/hierarchy model differs from TYPES-001 contract -> issue: SA-006-G07 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-C02]
- fact: DoExpr root composability contract absent in implementation type root -> issue: SA-006-G08 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G07]
- fact: callstack effect bridge does not follow unified DoCtrl path described in TYPES-001 -> issue: SA-006-G09 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-C02]
- fact: annotation alias normalization for `Thunk` diverges from spec example set -> issue: SA-006-G10 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G07]
- fact: scheduler ready-waiters queue behavior exists without explicit spec statement -> issue: SA-006-Q01 -> auto-resolve/discussion: discussion-required -> action: discuss -> dependencies: []
- fact: extra exported handler sentinels are visible publicly with unclear policy boundary -> issue: SA-006-Q02 -> auto-resolve/discussion: discussion-required -> action: discuss -> dependencies: []
- fact: test requirement location/scope policy for public API validation is underspecified vs existing tests -> issue: SA-006-Q03 -> auto-resolve/discussion: discussion-required -> action: discuss -> dependencies: []
