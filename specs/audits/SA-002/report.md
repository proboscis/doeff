# SA-002: Spec Audit Report

**Date:** 2026-02-08
**Session:** SA-002
**Specs audited:**
- `specs/vm/SPEC-008-rust-vm.md`
- `specs/vm/SPEC-009-rust-vm-migration.md`
- `specs/core/SPEC-TYPES-001-program-effect-separation.md`

**Implementation scope:**
- `doeff/`
- `packages/doeff-vm/`

**Review method:** 10 parallel section reviewers + cross-reference deduplication.

---

## Summary

| Category | Count |
|---|---:|
| Contradictions (C) | 1 |
| Gaps (G) | 9 |
| Discussion (Q) | 3 |

Gap severity split:

| Severity | Count |
|---|---:|
| Critical | 2 |
| Moderate | 5 |
| Minor | 2 |

---

## ASCII Gap Diagram

```text
+---------------------------------------------------------+
| SA-002 Spec Gap Map                                     |
+---------------------------------------------------------+
| C (spec contradiction)                                  |
|  SA-002-C01  KPC upgrade path inconsistent in TYPES-001 |
|                                                         |
| G (implementation gaps)                                 |
|  Critical: G01 G02                                      |
|  Moderate: G03 G04 G05 G06 G07                         |
|  Minor:    G08 G09                                      |
|                                                         |
| Q (spec silent / design decision)                       |
|  SA-002-Q01 Q02 Q03                                     |
+---------------------------------------------------------+
```

---

## Contradictions

| ID | Description | Spec refs | Why contradiction | Current impl behavior |
|---|---|---|---|---|
| SA-002-C01 | SPEC-TYPES-001 describes KPC as effect-dispatch path, but migration checklist says classify_yielded should upgrade KPC to Call. | `specs/core/SPEC-TYPES-001-program-effect-separation.md:619`, `specs/core/SPEC-TYPES-001-program-effect-separation.md:830` | Both cannot be primary semantics at once (effect-dispatch vs call-upgrade in classifier). | Impl follows effect-dispatch path (`packages/doeff-vm/src/pyvm.rs:723`). |

---

## Gaps

### Critical

| ID | Description | Spec refs | Impl refs | Enforcement |
|---|---|---|---|---|
| SA-002-G01 | `classify_yielded` still relies on import/getattr/marker fallback instead of pure base-type dispatch path. | `specs/vm/SPEC-008-rust-vm.md:991`, `specs/vm/SPEC-008-rust-vm.md:1005`, `specs/core/SPEC-TYPES-001-program-effect-separation.md:737` | `packages/doeff-vm/src/pyvm.rs:608`, `packages/doeff-vm/src/pyvm.rs:758` | test + semgrep |
| SA-002-G02 | KPC representation remains transitional: Rust `PyKPC` lacks required fielded shape/extends model and dispatch still reads optional strategy from object. | `specs/core/SPEC-TYPES-001-program-effect-separation.md:5`, `specs/core/SPEC-TYPES-001-program-effect-separation.md:482`, `specs/vm/SPEC-009-rust-vm-migration.md:44` | `packages/doeff-vm/src/effect.rs:30`, `packages/doeff-vm/src/handler.rs:195`, `doeff/program.py:489` | test + semgrep |

### Moderate

| ID | Description | Spec refs | Impl refs | Enforcement |
|---|---|---|---|---|
| SA-002-G03 | Implicit KPC install path remains in VM init (`PyVM::new`) despite explicit-install direction. | `specs/core/SPEC-TYPES-001-program-effect-separation.md:956`, `specs/vm/SPEC-009-rust-vm-migration.md:123` | `packages/doeff-vm/src/pyvm.rs:69` | test |
| SA-002-G04 | Public `doeff.RunResult` protocol surface is narrower than spec contract (`result/raw_store/error` missing from protocol). | `specs/vm/SPEC-009-rust-vm-migration.md:191` | `doeff/_types_internal.py:847`, `doeff/__init__.py:110` | test |
| SA-002-G05 | `default_handlers`/presets diverge from SPEC-009 section 7 expectations (bundle composition and sync/async distinction). | `specs/vm/SPEC-009-rust-vm-migration.md:679`, `specs/vm/SPEC-009-rust-vm-migration.md:692` | `doeff/rust_vm.py:35`, `doeff/presets.py:23` | test |
| SA-002-G06 | Scheduler completion path appears to remove waiters without explicit wake/resume enqueue in reviewed scheduler state path. | `specs/vm/SPEC-008-rust-vm.md:1797` | `packages/doeff-vm/src/scheduler.rs:409` | test |
| SA-002-G07 | DoCtrl pyclass inheritance is inconsistent with unified `DoCtrlBase` extension model (some controls do not extend). | `specs/vm/SPEC-008-rust-vm.md:946` | `packages/doeff-vm/src/pyvm.rs:1077`, `packages/doeff-vm/src/pyvm.rs:1097`, `packages/doeff-vm/src/pyvm.rs:1113` | test + semgrep |

### Minor

| ID | Description | Spec refs | Impl refs | Enforcement |
|---|---|---|---|---|
| SA-002-G08 | Legacy crate/file structure section in SPEC-008 does not match current consolidated implementation modules. | `specs/vm/SPEC-008-rust-vm.md:4701` | `packages/doeff-vm/src/lib.rs:12` | semgrep |
| SA-002-G09 | Strict Program input rule text diverges from current permissive `to_generator_strict` callable/DoExpr handling. | `specs/vm/SPEC-008-rust-vm.md:4690` | `packages/doeff-vm/src/pyvm.rs:517`, `packages/doeff-vm/src/pyvm.rs:532` | test |

---

## Discussion Items

| ID | Description | Impl refs |
|---|---|---|
| SA-002-Q01 | Scheduler fallback placeholder behavior in `doeff.handlers` (used only if VM export is unavailable) is not clearly covered by specs. | `doeff/handlers.py:22` |
| SA-002-Q02 | Public API is layered via both extension exports and Python wrappers; behavior is compatible but layering is undocumented. | `packages/doeff-vm/src/pyvm.rs:1734`, `doeff/rust_vm.py:45` |
| SA-002-Q03 | Transitional hybrid migration behavior (Python KPC + runtime rebasing + Rust alias exports) is not explicitly blessed/forbidden in specs. | `doeff/_types_internal.py:2156`, `packages/doeff-vm/src/pyvm.rs:1919` |

---

## Cross-Reference Notes

- False positive removed: A2 “GetHandlers retrieval missing” was discarded; retrieval exists in VM code (`packages/doeff-vm/src/vm.rs:1221`).
- Duplicates merged across A1/A3/B1/C1/C2 for classifier and KPC migration findings.
- External process refs collected for later phases (Semgrep rule testing and conformance-style gating): `https://semgrep.dev/docs/writing-rules/testing-rules`, `https://semgrep.dev/docs/running-rules`.
