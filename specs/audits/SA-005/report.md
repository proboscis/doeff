# SA-005 Spec Audit Report

- Date: 2026-02-08
- Session: SA-005
- Specs audited:
  - `specs/vm-architecture/SPEC-008-rust-vm.md`
  - `specs/vm-architecture/SPEC-009-rust-vm-migration.md`
  - `specs/SPEC-TYPES-001-program-effect-separation.md`
- Implementation scope:
  - `packages/doeff-vm/src`
  - `doeff`

## Summary

- Total classified items: 12
- Contradictions (C): 1
- Gaps (G): 8
  - Critical: 4
  - Moderate: 3
  - Minor: 1
- Discussion (Q): 3

## ASCII Gap Diagram

┌───────────────────────────────────────────────────────────────┐
│                 SPEC-vs-IMPL GAPS (SA-005)                   │
├───────────────────────────────────────────────────────────────┤
│ CONTRADICTIONS                                                │
│  └─ SA-005-C01: SPEC-008 internal conflict (Effect enum)     │
│                                                               │
│ CRITICAL                                                      │
│  ├─ SA-005-G01: Effect pyclass payload/shape drift           │
│  ├─ SA-005-G02: Scheduler effects missing as typed pyclasses │
│  ├─ SA-005-G04: Python-side normalization forbidden by spec  │
│  └─ SA-005-G07: Program=DoExpr hierarchy mismatch            │
│                                                               │
│ MODERATE                                                      │
│  ├─ SA-005-G03: Handler trait API drift (DispatchEffect)     │
│  ├─ SA-005-G05: run input/error-contract mismatch            │
│  └─ SA-005-G06: `kpc` missing from handlers export           │
│                                                               │
│ MINOR                                                         │
│  └─ SA-005-G08: Classifier flow shape drift (base-first)     │
│                                                               │
│ DISCUSSION                                                    │
│  ├─ SA-005-Q01: Preset/default-handler composition ambiguity │
│  ├─ SA-005-Q02: Continuation representation split            │
│  └─ SA-005-Q03: Callstack effect vs DoCtrl bridge            │
└───────────────────────────────────────────────────────────────┘

## Contradictions

| ID | Description | Spec refs | Impl chose | Resolution |
|---|---|---|---|---|
| SA-005-C01 | `SPEC-008` states Effect enum removed, but later scheduler snippets still use `Effect::...`/`&Effect` style signatures. | `specs/vm-architecture/SPEC-008-rust-vm.md:12`, `specs/vm-architecture/SPEC-008-rust-vm.md:1610` | Current code follows `DispatchEffect`/typed pyclass paths. | discuss |

## Gaps

### Critical

| ID | Description | Spec refs | Impl refs | Enforcement | Auto/default |
|---|---|---|---|---|---|
| SA-005-G01 | Standard effects are specified as typed payloaded pyclasses (`Get/Put/Modify/Ask/Tell`) but implementation uses zero-field marker-like forms in Rust effect structs. | `specs/vm-architecture/SPEC-008-rust-vm.md:823` | `packages/doeff-vm/src/effect.rs:16` | test + semgrep | auto-fix-code |
| SA-005-G02 | Scheduler effects required as typed pyclasses are missing from `effect.rs`; scheduler handler relies on marker attrs/parsing path. | `specs/vm-architecture/SPEC-008-rust-vm.md:864` | `packages/doeff-vm/src/effect.rs:16`, `packages/doeff-vm/src/scheduler.rs:145` | test + semgrep | auto-fix-code |
| SA-005-G04 | Spec says no Python-side normalization for top-level input; wrapper currently normalizes in Python before calling Rust VM. | `specs/vm-architecture/SPEC-009-rust-vm-migration.md:238` | `doeff/rust_vm.py:13`, `doeff/rust_vm.py:56` | test | auto-fix-code |
| SA-005-G07 | SPEC-TYPES hierarchy says `Program = DoExpr` root model, but implementation keeps `Program = ProgramBase` (`DoThunk`) alias model. | `specs/SPEC-TYPES-001-program-effect-separation.md:111` | `doeff/program.py:248`, `doeff/program.py:565` | test + semgrep | discussion-required |

### Moderate

| ID | Description | Spec refs | Impl refs | Enforcement | Auto/default |
|---|---|---|---|---|---|
| SA-005-G03 | Handler trait/API still centered on `DispatchEffect` signatures, differing from SPEC-008 opaque-bound effect contract text. | `specs/vm-architecture/SPEC-008-rust-vm.md:1127` | `packages/doeff-vm/src/handler.rs:42` | test | discussion-required |
| SA-005-G05 | `run` input/error contract differs from SPEC-009 wording (DoExpr acceptance boundary and error messaging expectations). | `specs/vm-architecture/SPEC-009-rust-vm-migration.md:203` | `doeff/rust_vm.py:27`, `packages/doeff-vm/src/pyvm.rs:508` | test | auto-fix-code |
| SA-005-G06 | SPEC-009 import table expects `kpc` from `doeff.handlers`; current handlers export omits it. | `specs/vm-architecture/SPEC-009-rust-vm-migration.md:746` | `doeff/handlers.py:7` | test | auto-fix-code |

### Minor

| ID | Description | Spec refs | Impl refs | Enforcement | Auto/default |
|---|---|---|---|---|---|
| SA-005-G08 | `classify_yielded` flow shape differs from strict base-first ordering text (concrete DoCtrl type ladder before broad base gate). | `specs/SPEC-TYPES-001-program-effect-separation.md:744` | `packages/doeff-vm/src/pyvm.rs:548` | semgrep | auto-fix-code |

## Discussion

| ID | Description | Impl refs | Resolution |
|---|---|---|---|
| SA-005-Q01 | `default_handlers`/`sync_preset` composition intent appears ambiguous across spec examples and current split implementation. | `doeff/presets.py:15`, `doeff/rust_vm.py:35` | discuss |
| SA-005-Q02 | Continuation representation remains split (`PyK` path vs dict conversion path), requiring policy decision on canonical interop shape. | `packages/doeff-vm/src/pyvm.rs:568`, `packages/doeff-vm/src/continuation.rs:130` | discuss |
| SA-005-Q03 | Callstack public API bridge remains mixed (`ProgramCallStackEffect` effect path vs `GetCallStack` DoCtrl path). | `doeff/effects/callstack.py:34`, `packages/doeff-vm/src/pyvm.rs:653` | discuss |

## Dependency Ledger (fact -> issue -> auto-resolve/discussion -> action -> dependencies)

- fact: SPEC-008 contains both "Effect enum removed" and legacy enum-typed snippets -> issue: SA-005-C01 -> auto-resolve/discussion: discussion-required -> action: discuss -> dependencies: []
- fact: effect pyclass payload mismatch in `effect.rs` vs SPEC-008 standard effect definitions -> issue: SA-005-G01 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-005-C01]
- fact: scheduler pyclass requirements absent from `effect.rs`; marker parsing in scheduler -> issue: SA-005-G02 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-005-C01]
- fact: handler trait contract mismatch between spec text and current `DispatchEffect` API -> issue: SA-005-G03 -> auto-resolve/discussion: discussion-required -> action: discuss -> dependencies: [SA-005-C01]
- fact: Python-side normalization exists in `doeff/rust_vm.py` despite SPEC-009 prohibition text -> issue: SA-005-G04 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: `run` input/error contract text and implementation behavior differ -> issue: SA-005-G05 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-005-G04]
- fact: `kpc` omitted in handlers export vs SPEC-009 imports table -> issue: SA-005-G06 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-005-Q01]
- fact: Program/DoExpr hierarchy mismatch between SPEC-TYPES and runtime alias model -> issue: SA-005-G07 -> auto-resolve/discussion: discussion-required -> action: discuss -> dependencies: [SA-005-C01]
- fact: classify flow shape differs from strict base-first ordering text -> issue: SA-005-G08 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-005-G07]
- fact: preset/default handler composition intent ambiguous across docs and split implementation -> issue: SA-005-Q01 -> auto-resolve/discussion: discussion-required -> action: discuss -> dependencies: []
- fact: continuation representation split path (`PyK`/dict) remains in code -> issue: SA-005-Q02 -> auto-resolve/discussion: discussion-required -> action: discuss -> dependencies: [SA-005-C01]
- fact: callstack effect-vs-doctrl bridge not unified -> issue: SA-005-Q03 -> auto-resolve/discussion: discussion-required -> action: discuss -> dependencies: [SA-005-G07]
