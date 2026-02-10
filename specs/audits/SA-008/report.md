# SA-008 Spec Audit Report

Date: 2026-02-08
Session: SA-008

Audited specs:
- `specs/vm/SPEC-008-rust-vm.md`
- `specs/vm/SPEC-009-rust-vm-migration.md`
- `specs/core/SPEC-TYPES-001-program-effect-separation.md`

Implementation directories:
- `doeff/`
- `packages/doeff-vm/`

## Summary

- Total items: 16
- Contradictions (C): 2
- Gaps (G): 11
  - Critical: 5
  - Moderate: 4
  - Minor: 2
- Discussion (Q): 3

## ASCII Gap Diagram

```text
┌──────────────────────────────────────────────────────────────┐
│                    SA-008 Gap Topology                       │
├──────────────────────────────────────────────────────────────┤
│ C: SPEC INTERNAL CONTRADICTIONS                              │
│  C01 DoThunk eliminated vs DoThunk-centric test language     │
│  C02 Strict binary classifier vs fallback/unknown wording    │
│                                                              │
│ G: IMPLEMENTATION GAPS                                       │
│  G01 Yielded::Unknown still present                          │
│  G02 classify_yielded still concrete/fallback-driven         │
│  G03 DoThunk compatibility alias still exported              │
│  G04 GeneratorProgram still core composition mechanism       │
│  G05 DoCtrl Map/FlatMap runtime semantics not implemented    │
│  G06 marker-based standard effect parsing remains            │
│  G07 marker-based scheduler effect parsing remains           │
│  G08 KPC parse strategy still shape/attribute-heavy          │
│  G09 RunResult surface split (Rust wrapper vs Python proto)  │
│  G10 Unhandled effect exposed as generic runtime error       │
│  G11 Internal/runtime import boundary remains weak           │
│                                                              │
│ Q: POLICY DECISIONS                                          │
│  Q01 Keep/remove DoThunk alias during migration              │
│  Q02 Keep/remove unknown fallback behavior                   │
│  Q03 Strict run boundary vs ergonomic wrapper behavior       │
└──────────────────────────────────────────────────────────────┘
```

## Contradictions (C)

| ID | Description | Spec refs | Why contradictory | Resolution |
|---|---|---|---|---|
| SA-008-C01 | R10 says DoThunk eliminated, while public test requirement text still includes DoThunk-centric hierarchy expectations | `SPEC-TYPES-001:7-9`, `SPEC-TYPES-001:1148-1150`, `SPEC-TYPES-001:1299-1310` | Normative direction and required test language diverge | fix-spec |
| SA-008-C02 | Classifier section mixes strict binary model language with fallback/unknown handling language | `SPEC-TYPES-001:922-947`, `SPEC-TYPES-001:1144-1148`, `SPEC-008:1117-1134` | One path requires strict binary classification; another leaves unknown/fallback path | fix-spec |

## Gaps (G)

### Critical

| ID | Description | Spec refs | Impl refs | Enforcement | Auto/default |
|---|---|---|---|---|---|
| SA-008-G01 | Runtime still includes `Yielded::Unknown`, diverging from strict binary DoExpr taxonomy | `SPEC-TYPES-001:928-947`, `SPEC-TYPES-001:1144-1148` | `packages/doeff-vm/src/yielded.rs:9` | test + semgrep | auto-fix-code |
| SA-008-G02 | `classify_yielded` remains concrete extraction + fallback path, not strict base/tag binary classifier | `SPEC-TYPES-001:922-1012`, `SPEC-008:1117-1134` | `packages/doeff-vm/src/pyvm.rs:568` | test + semgrep | auto-fix-code |
| SA-008-G04 | Python composition still generator-backed (`GeneratorProgram`) instead of pure DoCtrl node model | `SPEC-TYPES-001:878-921`, `SPEC-TYPES-001:1037-1046` | `doeff/program.py:324`, `doeff/program.py:336`, `doeff/program.py:452` | test | auto-fix-code |
| SA-008-G05 | Rust VM DoCtrl `Map/FlatMap` runtime semantics not implemented | `SPEC-008:4028-4046`, `SPEC-TYPES-001:674-699` | `packages/doeff-vm/src/vm.rs:517` | test | auto-fix-code |
| SA-008-G06 | Standard effect dispatch remains marker/getattr-based in handler parsing pipeline | `SPEC-008:807-892`, `SPEC-TYPES-001:929-947` | `packages/doeff-vm/src/handler.rs:110` | semgrep + test | auto-fix-code |

### Moderate

| ID | Description | Spec refs | Impl refs | Enforcement | Auto/default |
|---|---|---|---|---|---|
| SA-008-G03 | `DoThunk` compatibility alias remains exported in public API | `SPEC-TYPES-001:7-9`, `SPEC-TYPES-001:1008`, `SPEC-TYPES-001:1062` | `doeff/program.py:556`, `doeff/program.py:561` | test + semgrep | auto-fix-code |
| SA-008-G07 | Scheduler effect parsing remains marker-based/attribute-driven | `SPEC-008:864-907`, `SPEC-008:1117-1134` | `packages/doeff-vm/src/scheduler.rs:145` | semgrep + test | auto-fix-code |
| SA-008-G08 | KPC parse/unwrap strategy still shape/attribute-heavy and mixed Python/Rust responsibilities | `SPEC-TYPES-001:371-550`, `SPEC-TYPES-001:922-1012` | `packages/doeff-vm/src/handler.rs:182`, `doeff/program.py:214` | test | auto-fix-code |
| SA-008-G09 | RunResult surface remains split between Rust result wrappers and Python-side protocol expectations | `SPEC-009:260-330` | `packages/doeff-vm/src/pyvm.rs:852`, `doeff/_types_internal.py:868` | test | auto-fix-code |

### Minor

| ID | Description | Spec refs | Impl refs | Enforcement | Auto/default |
|---|---|---|---|---|---|
| SA-008-G10 | Unhandled effects are exposed as generic runtime/type errors instead of dedicated domain error surface | `SPEC-009:143-259` | `packages/doeff-vm/src/vm.rs:1005`, `packages/doeff-vm/src/error.rs:36` | test | auto-fix-code |
| SA-008-G11 | Runtime internals are still import-discoverable from extension layer; policy boundary remains weak | `SPEC-009:840-882` | `packages/doeff-vm/doeff_vm/__init__.py:7` | semgrep | auto-fix-code |

## Discussion (Q)

| ID | Description | Impl refs | Why discussion |
|---|---|---|---|
| SA-008-Q01 | Keep or remove DoThunk alias during migration window | `doeff/program.py:556`, `tests/public_api/test_types_001_hierarchy.py:12` | Compatibility vs strict R10 compliance tradeoff |
| SA-008-Q02 | Keep or remove unknown fallback classifier behavior | `packages/doeff-vm/src/yielded.rs:9`, `packages/doeff-vm/src/pyvm.rs:685` | Operational safety fallback vs strict spec behavior |
| SA-008-Q03 | Keep strict explicit run boundary only, or preserve higher-level ergonomic wrappers with defaults | `doeff/run.py:447`, `doeff/rust_vm.py:48` | Usability vs normative strictness scope |

## Blocking Dependency Resolution Ledger

- fact: TYPES-001 says DoThunk eliminated while test requirement text still mentions DoThunk hierarchy -> issue: SA-008-C01 -> auto-resolve/discussion: discussion-required -> action: fix-spec -> dependencies: []
- fact: classifier sections mix strict binary and unknown/fallback semantics -> issue: SA-008-C02 -> auto-resolve/discussion: discussion-required -> action: fix-spec -> dependencies: []
- fact: runtime Yielded enum retains Unknown variant -> issue: SA-008-G01 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-008-C02]
- fact: classify_yielded still concrete/fallback-based -> issue: SA-008-G02 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-008-C02, SA-008-G01]
- fact: DoThunk alias still exported -> issue: SA-008-G03 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-008-C01]
- fact: composition still generator-backed in Program layer -> issue: SA-008-G04 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-008-C01, SA-008-G03]
- fact: Rust DoCtrl Map/FlatMap runtime path throws unimplemented -> issue: SA-008-G05 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-008-G04]
- fact: standard effect parse path remains marker/getattr-based -> issue: SA-008-G06 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-008-C02]
- fact: scheduler effect parse path remains marker/getattr-based -> issue: SA-008-G07 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-008-C02, SA-008-G06]
- fact: KPC parse/unwrap strategy mixed across Python and Rust assumptions -> issue: SA-008-G08 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-008-G02, SA-008-G04]
- fact: RunResult surface split between Rust wrappers and Python protocol -> issue: SA-008-G09 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: unhandled effect currently surfaced as generic runtime/type error -> issue: SA-008-G10 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: extension-layer internals remain import-discoverable -> issue: SA-008-G11 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: DoThunk alias may be needed for compatibility window -> issue: SA-008-Q01 -> auto-resolve/discussion: discussion-required -> action: remove-from-code -> dependencies: [SA-008-C01]
- fact: Unknown fallback may be desired for operational safety -> issue: SA-008-Q02 -> auto-resolve/discussion: discussion-required -> action: fix-both -> dependencies: [SA-008-C02]
- fact: high-level run wrappers may intentionally trade strictness for ergonomics -> issue: SA-008-Q03 -> auto-resolve/discussion: discussion-required -> action: fix-both -> dependencies: []
