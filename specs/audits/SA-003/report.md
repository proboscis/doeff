# SA-003: Spec Audit Report

**Date:** 2026-02-08
**Session:** SA-003
**Specs audited:**
- `specs/vm-architecture/SPEC-008-rust-vm.md`
- `specs/vm-architecture/SPEC-009-rust-vm-migration.md`
- `specs/SPEC-TYPES-001-program-effect-separation.md`

**Implementation scope:**
- `doeff/`
- `packages/doeff-vm/`

**Review method:** 9 parallel section reviewers (U1-U9) + direct grep/ast-grep cross-check + deduplication.

---

## Summary

| Category | Count |
|---|---:|
| Contradictions (C) | 1 |
| Gaps (G) | 8 |
| Discussion (Q) | 4 |

Gap severity split:

| Severity | Count |
|---|---:|
| Critical | 2 |
| Moderate | 4 |
| Minor | 2 |

---

## ASCII Gap Diagram

```text
+--------------------------------------------------------------+
| SA-003 Spec Gap Map                                          |
+--------------------------------------------------------------+
| C (spec contradiction)                                       |
|  SA-003-C01                                                   |
|                                                              |
| G (implementation gaps)                                      |
|  Critical: SA-003-G01 SA-003-G02                             |
|  Moderate: SA-003-G03 SA-003-G04 SA-003-G05 SA-003-G06      |
|  Minor:    SA-003-G07 SA-003-G08                             |
|                                                              |
| Q (spec silent / policy decision)                            |
|  SA-003-Q01 SA-003-Q02 SA-003-Q03 SA-003-Q04                |
+--------------------------------------------------------------+
```

---

## Contradictions

| ID | Description | Spec refs | Why contradiction | Current impl behavior |
|---|---|---|---|---|
| SA-003-C01 | Python base-class ownership was inconsistent across specs: one path required deleting Python Effect/DoCtrl/DoThunk bases and importing Rust bases, while TYPES-001 still described Python-side subclassing. | `specs/vm-architecture/SPEC-008-rust-vm.md:30`, `specs/SPEC-TYPES-001-program-effect-separation.md:20` | Both cannot be primary architecture simultaneously. Strict source-of-truth is SPEC-008 (no transitional mode). | Resolved at spec level by aligning TYPES-001 to strict Rust-owned base-class policy. |

---

## Gaps

### Critical

| ID | Description | Spec refs | Impl refs | Enforcement |
|---|---|---|---|---|
| SA-003-G01 | `DoCtrl::Call`/`CallFunc` path can be routed into `PendingPython::StartProgramFrame`, but result handling for that pending state expects a generator and can reject value-returning `CallFunc` outcomes. | `specs/vm-architecture/SPEC-008-rust-vm.md:2489`, `specs/vm-architecture/SPEC-008-rust-vm.md:2500` | `packages/doeff-vm/src/vm.rs:535`, `packages/doeff-vm/src/vm.rs:630`, `packages/doeff-vm/src/pyvm.rs:433` | test |
| SA-003-G02 | Driver Python-call error normalization diverges from spec paths requiring `PyCallOutcome::GenError` for invalid StartProgram/CallHandler/CallAsync outcomes; current paths can return `PyErr` directly. | `specs/vm-architecture/SPEC-008-rust-vm.md:3033`, `specs/vm-architecture/SPEC-008-rust-vm.md:3051`, `specs/vm-architecture/SPEC-008-rust-vm.md:3068` | `packages/doeff-vm/src/pyvm.rs:428`, `packages/doeff-vm/src/pyvm.rs:459`, `packages/doeff-vm/src/pyvm.rs:491` | test |

### Moderate

| ID | Description | Spec refs | Impl refs | Enforcement |
|---|---|---|---|---|
| SA-003-G03 | Scheduler isolated-store resume fallback silently downgrades to shared-store semantics when snapshot missing; spec requires runtime error for invalid isolated state. | `specs/vm-architecture/SPEC-008-rust-vm.md:1754` | `packages/doeff-vm/src/scheduler.rs:762` | test |
| SA-003-G04 | `GetHandlers` skips missing handler entries instead of failing; spec path requires runtime error when handler lookup fails. | `specs/vm-architecture/SPEC-008-rust-vm.md:4251` | `packages/doeff-vm/src/vm.rs:1135` | test |
| SA-003-G05 | Standard handler installation still has direct stdlib install path (`run_scoped` booleans / `PyStdlib.install_*`) instead of fully unified explicit handler list protocol from ADR guidance. | `specs/vm-architecture/SPEC-008-rust-vm.md:131`, `specs/vm-architecture/SPEC-008-rust-vm.md:245` | `packages/doeff-vm/src/pyvm.rs:329`, `packages/doeff-vm/src/pyvm.rs:761` | test + semgrep |
| SA-003-G06 | Async control primitive naming drift: spec names `PythonAsyncSyntaxEscape`, implementation publicly exposes `AsyncEscape`; wrapper boundary does not expose spec name. | `specs/vm-architecture/SPEC-008-rust-vm.md:3625`, `specs/vm-architecture/SPEC-009-rust-vm-migration.md:186` | `packages/doeff-vm/src/pyvm.rs:1100`, `packages/doeff-vm/doeff_vm/__init__.py:34`, `doeff/rust_vm.py:74` | test |

### Minor

| ID | Description | Spec refs | Impl refs | Enforcement |
|---|---|---|---|---|
| SA-003-G07 | Crate structure section diverges from actual current module layout (`handlers/mod.rs` tree vs consolidated `handler.rs`, `step.rs` as shim). | `specs/vm-architecture/SPEC-008-rust-vm.md:4717` | `packages/doeff-vm/src/lib.rs:12`, `packages/doeff-vm/src/step.rs:1` | semgrep |
| SA-003-G08 | Invariant text says GIL released during Rust handler execution; dispatch currently attaches GIL when calling `guard.start(...)`. | `specs/vm-architecture/SPEC-008-rust-vm.md:4453` | `packages/doeff-vm/src/vm.rs:861`, `packages/doeff-vm/src/vm.rs:1067` | test |

---

## Discussion Outcomes (Strict)

| ID | Outcome | Evidence |
|---|---|---|
| SA-003-Q01 | No action required. Strict spec already defines `run`/`async_run` as public boundary; `PyVM` remains implementation-layer. | `specs/vm-architecture/SPEC-009-rust-vm-migration.md:748` |
| SA-003-Q02 | No spec change required. Runtime enforces DoExpr semantics and rejects non-DoExpr objects. | `doeff/rust_vm.py:21`, `specs/vm-architecture/SPEC-009-rust-vm-migration.md:137` |
| SA-003-Q03 | No action required. Callback table is an internal mechanism, not a user-facing API surface. | `packages/doeff-vm/src/vm.rs:95`, `specs/vm-architecture/SPEC-009-rust-vm-migration.md:748` |
| SA-003-Q04 | Enforced strict classifier behavior in code (no getattr/import/string-based classification metadata path). | `packages/doeff-vm/src/pyvm.rs:669`, `specs/vm-architecture/SPEC-008-rust-vm.md:1005` |

---

## Cross-Reference Notes

- Multiple unit reports overlapped with already-addressed SA-002 topics; duplicates were collapsed into SA-003 IDs above.
- Q-items were resolved under strict reading of SPEC-008/SPEC-009; no fallback or softening interpretation is retained.
- External references gathered for conformance/TDD process quality align with current repo approach (test-first + semgrep enforcement), and are captured as process guidance only.
