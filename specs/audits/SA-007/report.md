# SA-007 Spec Audit Report

Date: 2026-02-08
Session: SA-007

Audited specs:
- `specs/vm-architecture/SPEC-008-rust-vm.md`
- `specs/vm-architecture/SPEC-009-rust-vm-migration.md`
- `specs/SPEC-TYPES-001-program-effect-separation.md`

Implementation directories:
- `doeff/`
- `packages/doeff-vm/`

## Summary

- Total items: 14
- Contradictions (C): 3
- Gaps (G): 8
  - Critical: 4
  - Moderate: 3
  - Minor: 1
- Discussion (Q): 3

## ASCII Gap Diagram

```text
┌───────────────────────────────────────────────────────────┐
│                   SPEC-vs-IMPL GAPS (SA-007)             │
├───────────────────────────────────────────────────────────┤
│ CONTRADICTIONS                                            │
│ ├─ SA-007-C01: WithHandler field naming conflict         │
│ ├─ SA-007-C02: default_handlers KPC conflict in TYPES    │
│ └─ SA-007-C03: classifier GIL-free vs fallback conflict  │
│                                                           │
│ CRITICAL                                                  │
│ ├─ SA-007-G01: run() boundary normalizes input           │
│ ├─ SA-007-G02: public input validation not strict DoExpr │
│ ├─ SA-007-G03: constructor-time primitive validation gap  │
│ └─ SA-007-G04: DoThunk/to_generator pipeline still core  │
│                                                           │
│ MODERATE                                                  │
│ ├─ SA-007-G05: Pure/Map/FlatMap DoCtrl model missing     │
│ ├─ SA-007-G06: top-level DoCtrl acceptance drift         │
│ └─ SA-007-G07: GetHandlers identity placeholder output   │
│                                                           │
│ MINOR                                                     │
│ └─ SA-007-G08: classifier architecture drift (base-only) │
│                                                           │
│ DISCUSSION                                                │
│ ├─ SA-007-Q01: extra public operational PyVM methods     │
│ ├─ SA-007-Q02: `kpc` export location policy              │
│ └─ SA-007-Q03: arena/free-list internals not in spec     │
└───────────────────────────────────────────────────────────┘
```

## Contradictions (C)

| ID | Description | Spec refs | Why contradictory | Impl chose | Resolution |
|---|---|---|---|---|---|
| SA-007-C01 | `WithHandler` field name alternates between `expr` and `program` | `SPEC-008:303-304`, `SPEC-008:1186-1188`, `SPEC-008:195-198` | Two incompatible API names in one spec version | Implementation uses `.program` | fix-spec (`expr: DoExpr`) |
| SA-007-C02 | TYPES default-handlers docs conflict on KPC inclusion | `SPEC-TYPES-001:1337`, `SPEC-TYPES-001:1372` | One section requires KPC in defaults, another table lists only state/reader/writer | Implementation includes KPC in defaults | fix-spec (KPC in defaults) |
| SA-007-C03 | Classifier says no `is_instance_of` fallback, but fallback text permits it | `SPEC-008:1095-1096`, `SPEC-008:1117-1164` | The same section both forbids and allows fallback-based type checks | Implementation uses fallback-style checks | fix-spec (no fallback, tag-only) |

## Gaps (G)

### Critical

| ID | Description | Spec refs | Impl refs | Enforcement | Auto/default |
|---|---|---|---|---|---|
| SA-007-G01 | Python entry boundary still normalizes/wraps input before Rust VM (`_normalize_program`, `_TopLevelDoExpr`) | `SPEC-009:240-245`, `SPEC-009:143-178` | `doeff/rust_vm.py:13`, `doeff/rust_vm.py:22` | test | auto-fix-code |
| SA-007-G02 | Public API input validation is duck-typed (protocol-based) instead of strict DoExpr boundary with spec-grade errors | `SPEC-009:203-219`, `SPEC-009:983-1011` | `doeff/rust_vm.py:23`, `doeff/rust_vm.py:31`, `packages/doeff-vm/src/pyvm.rs:507` | test | auto-fix-code |
| SA-007-G03 | `Resume`/`Transfer`/`Delegate`/`WithHandler` construction-time validation not enforced at constructors | `SPEC-009:1041-1058`, `SPEC-009:1017-1020` | `packages/doeff-vm/src/pyvm.rs:947`, `packages/doeff-vm/src/pyvm.rs:964`, `packages/doeff-vm/src/pyvm.rs:1003` | test + semgrep | auto-fix-code |
| SA-007-G04 | TYPE-001 elimination target not met (`DoThunk`/`to_generator` path remains central) | `SPEC-TYPES-001:303-370`, `SPEC-TYPES-001:878-921` | `doeff/program.py:255`, `doeff/program.py:565`, `packages/doeff-vm/src/pyvm.rs:507` | test + semgrep | auto-fix-code |

### Moderate

| ID | Description | Spec refs | Impl refs | Enforcement | Auto/default |
|---|---|---|---|---|---|
| SA-007-G05 | `Pure/Map/FlatMap` DoCtrl model is missing; composition remains generator wrapper-based | `SPEC-TYPES-001:878-921`, `SPEC-008:3995-4040` | `packages/doeff-vm/src/do_ctrl.rs:13`, `doeff/program.py:342`, `doeff/program.py:360` | test + semgrep | auto-fix-code |
| SA-007-G06 | Top-level DoCtrl acceptance contract drifts from spec narrative | `SPEC-009:204-244`, `SPEC-009:331-389` | `packages/doeff-vm/src/pyvm.rs:507`, `doeff/rust_vm.py:23` | test | auto-fix-code |
| SA-007-G07 | Handler identity preservation drifts (`"rust_program_handler"` placeholder output) | `SPEC-008:1219-1221`, `SPEC-008:4595-4617` | `packages/doeff-vm/src/value.rs:50`, `packages/doeff-vm/src/continuation.rs:48` | test | auto-fix-code |

### Minor

| ID | Description | Spec refs | Impl refs | Enforcement | Auto/default |
|---|---|---|---|---|---|
| SA-007-G08 | Classifier architecture remains concrete-type driven, not strict base-only path | `SPEC-008:1117-1164`, `SPEC-TYPES-001:922-1012` | `packages/doeff-vm/src/pyvm.rs:566` | semgrep | auto-fix-code |

## Discussion (Q)

| ID | Description | Impl refs | Why discussion |
|---|---|---|---|
| SA-007-Q01 | Extra public operational methods on VM boundary (`step_once`, `feed_async_result`, `enable_debug`, etc.) | `packages/doeff-vm/src/pyvm.rs:189`, `packages/doeff-vm/src/pyvm.rs:271`, `packages/doeff-vm/src/pyvm.rs:305` | Spec lists only user-facing public API; policy needed whether to hide or document |
| SA-007-Q02 | `kpc` export location policy (`doeff.handlers` explicit export) | `doeff/handlers.py:7`, `doeff/__init__.py:198` | Spec is not explicit on preferred export surface for KPC sentinel |
| SA-007-Q03 | Arena/free-list internals are implementation detail but spec text may imply stricter structure | `packages/doeff-vm/src/arena.rs:6`, `packages/doeff-vm/src/vm.rs:152` | Determine whether to codify as non-normative detail or align text strictly |

## Blocking Dependency Resolution Ledger

- fact: SPEC-008 uses both `expr` and `program` field naming for `WithHandler` -> issue: SA-007-C01 -> auto-resolve/discussion: discussion-required -> action: fix-spec -> dependencies: []
- fact: TYPES default-handler requirements conflict over KPC inclusion -> issue: SA-007-C02 -> auto-resolve/discussion: discussion-required -> action: fix-spec -> dependencies: []
- fact: classifier section both forbids and allows fallback-style checks -> issue: SA-007-C03 -> auto-resolve/discussion: discussion-required -> action: fix-spec -> dependencies: []
- fact: Python boundary normalizes/wraps top-level inputs -> issue: SA-007-G01 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-007-C01]
- fact: boundary validation and diagnostics are not strict DoExpr contract -> issue: SA-007-G02 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-007-C01]
- fact: constructor-time primitive validation is absent -> issue: SA-007-G03 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-007-G01, SA-007-G02]
- fact: DoThunk/to_generator remains central despite TYPE-001 direction -> issue: SA-007-G04 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-007-C02]
- fact: DoCtrl `Pure/Map/FlatMap` model missing -> issue: SA-007-G05 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-007-G04]
- fact: top-level DoCtrl acceptance contract mismatch -> issue: SA-007-G06 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-007-G01, SA-007-G05]
- fact: handler identity path emits placeholders -> issue: SA-007-G07 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: classifier remains concrete-type driven vs base-only architecture -> issue: SA-007-G08 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-007-C03, SA-007-G04]
- fact: extra operational VM methods are publicly reachable -> issue: SA-007-Q01 -> auto-resolve/discussion: discussion-required -> action: remove-from-code -> dependencies: []
- fact: `kpc` export surface is policy-ambiguous -> issue: SA-007-Q02 -> auto-resolve/discussion: discussion-required -> action: add-to-spec -> dependencies: []
- fact: arena internals vs normative wording is unclear -> issue: SA-007-Q03 -> auto-resolve/discussion: discussion-required -> action: add-to-spec -> dependencies: []
