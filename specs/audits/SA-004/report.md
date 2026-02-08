# SA-004: Spec Audit Report

**Date:** 2026-02-08
**Session:** SA-004
**Specs audited:**
- `specs/vm-architecture/SPEC-008-rust-vm.md`
- `specs/vm-architecture/SPEC-009-rust-vm-migration.md`
- `specs/SPEC-TYPES-001-program-effect-separation.md`

**Implementation scope:**
- `doeff/`
- `packages/doeff-vm/`

**Review method:** parallel section reviewers (U1-U6) + direct grep/ast-grep + cross-reference against SA-001..SA-003.

---

## Summary

Phase 1 completed with six parallel review units (U1-U6) and direct verification.
Phase 2 classification completed with dedup/carry-over filtering against SA-001..SA-003.

| Category | Count |
|---|---:|
| Contradictions (C) | 0 |
| Gaps (G) | 8 |
| Discussion (Q) | 4 |

---

## ASCII Gap Diagram

```text
+--------------------------------------------------------------+
| SA-004 Spec Gap Map                                          |
+--------------------------------------------------------------+
| C: none                                                      |
| G: SA-004-G01 SA-004-G02 SA-004-G03 SA-004-G04              |
|    SA-004-G05 SA-004-G06 SA-004-G07 SA-004-G08              |
| Q: SA-004-Q01 SA-004-Q02 SA-004-Q03 SA-004-Q04              |
+--------------------------------------------------------------+
```

---

## Contradictions

No direct spec contradictions were confirmed in SA-004.

## Gaps

| ID | Description | Spec refs | Impl refs | Severity | Enforcement |
|---|---|---|---|---|---|
| SA-004-G01 | `SPEC-008` defines fielded Rust effect pyclasses (`Get/Put/Modify/Ask/Tell`) while implementation keeps marker pyclasses and extracts payload from Python effect objects. | `specs/vm-architecture/SPEC-008-rust-vm.md:821` | `packages/doeff-vm/src/effect.rs:16`, `packages/doeff-vm/src/handler.rs:110` | Critical | test + semgrep |
| SA-004-G02 | `SPEC-008` defines typed scheduler pyclasses (`Spawn/Gather/Race/...`) while implementation parses scheduler marker attrs in handler code. | `specs/vm-architecture/SPEC-008-rust-vm.md:862` | `packages/doeff-vm/src/scheduler.rs:145` | Critical | test + semgrep |
| SA-004-G03 | Handler trait signature text in `SPEC-008` differs from current `DispatchEffect`-based implementation. | `specs/vm-architecture/SPEC-008-rust-vm.md:1121` | `packages/doeff-vm/src/handler.rs:43`, `packages/doeff-vm/src/handler.rs:50` | Moderate | fix-spec or fix-code |
| SA-004-G04 | Preset docs/example indicate sync preset scheduler inclusion; actual `sync_preset` resolves to `default_handlers()` without scheduler. | `specs/vm-architecture/SPEC-009-rust-vm-migration.md:686` | `doeff/presets.py:17`, `doeff/rust_vm.py:34` | Moderate | test + fix-spec/fix-code |
| SA-004-G05 | Entrypoint typing drift: spec text uses `handlers: list[Handler] = []` while API wrapper uses `Sequence[Any] = ()`. | `specs/vm-architecture/SPEC-009-rust-vm-migration.md:140` | `doeff/rust_vm.py:48` | Minor | fix-spec |
| SA-004-G06 | `Modify` parameter naming drift (`fn` in spec text vs `f`/`func` in implementation). | `specs/vm-architecture/SPEC-009-rust-vm-migration.md:353` | `doeff/effects/state.py:45`, `doeff/effects/state.py:72` | Minor | fix-spec |
| SA-004-G07 | SPEC-TYPES naming drift: `DerivedProgram` vs implementation `GeneratorProgram` for derived/map wrappers. | `specs/SPEC-TYPES-001-program-effect-separation.md:500` | `doeff/program.py:284`, `doeff/_types_internal.py:617` | Minor | fix-spec |
| SA-004-G08 | Test-only KPC parse path still reads `auto_unwrap_strategy` attribute; production path derives strategy from `kleisli_source`. | `specs/SPEC-TYPES-001-program-effect-separation.md:320` | `packages/doeff-vm/src/handler.rs:247`, `packages/doeff-vm/src/handler.rs:191` | Minor | test + semgrep |

## Discussion Items

| ID | Description | Spec refs | Impl refs | Suggested resolution |
|---|---|---|---|---|
| SA-004-Q01 | `RunResult.result` wrapper representation (`PyResultOk/PyResultErr` aliases) is semantically aligned but under-documented in spec text. | `specs/vm-architecture/SPEC-009-rust-vm-migration.md:225` | `packages/doeff-vm/src/pyvm.rs:801`, `doeff/__init__.py:143` | add-to-spec |
| SA-004-Q02 | Pure-value semantics decision: SPEC-TYPES `PureProgram` wording vs implementation `PureEffect` architecture. | `specs/SPEC-TYPES-001-program-effect-separation.md:188` | `doeff/effects/pure.py:12`, `doeff/program.py:369` | discuss (fix-spec vs fix-code) |
| SA-004-Q03 | Continuation handle representation appears split (`PyK` expected in `Resume/Transfer`, dict conversion path in `Continuation::to_pyobject`). Needs behavior confirmation. | `specs/vm-architecture/SPEC-008-rust-vm.md:3601` | `packages/doeff-vm/src/pyvm.rs:568`, `packages/doeff-vm/src/continuation.rs:130` | discuss + targeted failing test |
| SA-004-Q04 | Explicit KPC handler policy remains a carry-over decision from prior deferred item (no clear SA-004 regression evidence yet). | `specs/SPEC-TYPES-001-program-effect-separation.md:961` | `tests/core/test_sa002_spec_gaps.py:58`, `specs/audits/SA-001/progress.md:78` | discuss |

## Reviewer Notes Received

- U3 completed (`bg_6b103c72`): SPEC-009 sections 0-5 mostly conformant with two minor drifts and one clarification item.
- U1 completed (`bg_29e3a166`): SPEC-008 lines 1-1800 yielded multiple structural drift candidates; one false-positive (`Value::Handlers`) removed after direct verification.
- U4 completed (`bg_a53fb5e9`): SPEC-009 sections 6-11 mostly conformant with one moderate preset-composition divergence candidate.
- U5 completed (`bg_ff7450d0`): SPEC-TYPES-001 sections 1-4 mostly conformant with two moderate/minor structural-naming candidates plus one test-path discussion item.
- U6 completed (`bg_dd87da5f`): SPEC-TYPES-001 sections 5-10 call-stack/classifier paths largely align; added continuation/K handle discussion candidate.
- U2 completed (`bg_9ce924e6`): SPEC-008 execution/state-machine/invariant review delivered; several claims were filtered as non-issues after direct file verification.
