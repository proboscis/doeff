# SA-007 Progress

Date: 2026-02-08
Session: SA-007

Specs:
- `specs/vm/SPEC-008-rust-vm.md`
- `specs/vm/SPEC-009-rust-vm-migration.md`
- `specs/core/SPEC-TYPES-001-program-effect-separation.md`

## Status Table

| ID | Status | Test/Rule | Fix PR |
|---|---|---|---|
| SA-007-C01 | fixed-spec | `specs/vm/SPEC-008-rust-vm.md` |  |
| SA-007-C02 | fixed-spec | `specs/core/SPEC-TYPES-001-program-effect-separation.md` |  |
| SA-007-C03 | fixed-spec | `specs/vm/SPEC-008-rust-vm.md` |  |
| SA-007-G01 | reviewed-correct | `tests/core/test_sa007_spec_gaps.py::test_SA_007_G01_no_top_level_normalization_wrapper` |  |
| SA-007-G02 | reviewed-correct | `tests/core/test_sa007_spec_gaps.py::test_SA_007_G02_run_typeerror_includes_actionable_hints` |  |
| SA-007-G03 | reviewed-correct | `tests/core/test_sa007_spec_gaps.py::test_SA_007_G03_withhandler_constructor_validates_handler_type`, `tests/core/test_sa007_spec_gaps.py::test_SA_007_G03_resume_constructor_validates_k_handle`, `specs/audits/SA-007/semgrep.yml#spec-gap-SA-007-G03-no-raw-any-constructors` |  |
| SA-007-G04 | reviewed-correct | `tests/core/test_sa007_spec_gaps.py::test_SA_007_G04_dothunk_removed_from_public_hierarchy`, `specs/audits/SA-007/semgrep.yml#spec-gap-SA-007-G04-no-dothunk-class` |  |
| SA-007-G05 | reviewed-correct | `tests/core/test_sa007_spec_gaps.py::test_SA_007_G05_docontrol_has_pure_map_flatmap_nodes` |  |
| SA-007-G06 | reviewed-correct | `tests/core/test_sa007_spec_gaps.py::test_SA_007_G06_run_accepts_top_level_withhandler_expr` |  |
| SA-007-G07 | reviewed-correct | `tests/core/test_sa007_spec_gaps.py::test_SA_007_G07_get_handlers_preserves_identity_not_placeholder`, `specs/audits/SA-007/semgrep.yml#spec-gap-SA-007-G07-no-placeholder-handler-identity` |  |
| SA-007-G08 | reviewed-correct | `tests/core/test_sa007_spec_gaps.py::test_SA_007_G08_classifier_avoids_concrete_doctrl_type_checks`, `specs/audits/SA-007/semgrep.yml#spec-gap-SA-007-G08-no-concrete-classifier-branches` |  |
| SA-007-Q01 | resolved-remove-from-code |  |  |
| SA-007-Q02 | fixed-spec | `specs/vm/SPEC-009-rust-vm-migration.md` |  |
| SA-007-Q03 | fixed-spec | `specs/vm/SPEC-008-rust-vm.md` |  |

## Dependency/Status Ledger

- fact: SPEC-008 uses both `expr` and `program` for `WithHandler` -> issue: SA-007-C01 -> auto-resolve/discussion: discussion-required -> action: fix-spec -> dependencies: []
- fact: TYPES defaults conflict on KPC inclusion -> issue: SA-007-C02 -> auto-resolve/discussion: discussion-required -> action: fix-spec -> dependencies: []
- fact: classifier section both forbids and permits fallback checks -> issue: SA-007-C03 -> auto-resolve/discussion: discussion-required -> action: fix-spec -> dependencies: []
- fact: entry boundary performs Python normalization/wrapping -> issue: SA-007-G01 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-007-C01]
- fact: boundary validation uses duck-typing and weaker diagnostics -> issue: SA-007-G02 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-007-C01]
- fact: constructor-time primitive validation missing -> issue: SA-007-G03 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-007-G01, SA-007-G02]
- fact: DoThunk/to_generator architecture still active -> issue: SA-007-G04 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-007-C02]
- fact: Pure/Map/FlatMap DoCtrl model missing -> issue: SA-007-G05 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-007-G04]
- fact: top-level DoCtrl acceptance diverges -> issue: SA-007-G06 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-007-G01, SA-007-G05]
- fact: GetHandlers identity path emits placeholder string -> issue: SA-007-G07 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: classifier remains concrete-type driven -> issue: SA-007-G08 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-007-C03, SA-007-G04]
- fact: extra public PyVM operational methods exist -> issue: SA-007-Q01 -> auto-resolve/discussion: discussion-required -> action: remove-from-code -> dependencies: []
- fact: `kpc` export surface policy is unspecified -> issue: SA-007-Q02 -> auto-resolve/discussion: discussion-required -> action: add-to-spec -> dependencies: []
- fact: arena/free-list policy is unspecified -> issue: SA-007-Q03 -> auto-resolve/discussion: discussion-required -> action: add-to-spec -> dependencies: []

## Phase 3 TDD Plan

| ID | Enforcement | Planned artifact | Why this enforcement |
|---|---|---|---|
| SA-007-G01 | test | `tests/core/test_sa007_spec_gaps.py::test_SA_007_G01_no_top_level_normalization_wrapper` | Boundary behavior is observable through source/API contract |
| SA-007-G02 | test | `tests/core/test_sa007_spec_gaps.py::test_SA_007_G02_run_typeerror_includes_actionable_hints` | Runtime validation contract and error quality are behavioral |
| SA-007-G03 | test + semgrep | `tests/core/test_sa007_spec_gaps.py::test_SA_007_G03_withhandler_constructor_validates_handler_type`, `tests/core/test_sa007_spec_gaps.py::test_SA_007_G03_resume_constructor_validates_k_handle`, `specs/audits/SA-007/semgrep.yml#spec-gap-SA-007-G03-no-raw-any-constructors` | Constructor boundary should fail fast and structurally avoid untyped constructor signatures |
| SA-007-G04 | test + semgrep | `tests/core/test_sa007_spec_gaps.py::test_SA_007_G04_dothunk_removed_from_public_hierarchy`, `specs/audits/SA-007/semgrep.yml#spec-gap-SA-007-G04-no-dothunk-class` | Architectural elimination target needs both behavioral and structural guard |
| SA-007-G05 | test | `tests/core/test_sa007_spec_gaps.py::test_SA_007_G05_docontrol_has_pure_map_flatmap_nodes` | Missing control-node model is directly observable in core control type definitions |
| SA-007-G06 | test | `tests/core/test_sa007_spec_gaps.py::test_SA_007_G06_run_accepts_top_level_withhandler_expr` | Public run contract for top-level DoCtrl is behavioral |
| SA-007-G07 | test + semgrep | `tests/core/test_sa007_spec_gaps.py::test_SA_007_G07_get_handlers_preserves_identity_not_placeholder`, `specs/audits/SA-007/semgrep.yml#spec-gap-SA-007-G07-no-placeholder-handler-identity` | Identity fidelity is behavioral and should be blocked structurally |
| SA-007-G08 | test + semgrep | `tests/core/test_sa007_spec_gaps.py::test_SA_007_G08_classifier_avoids_concrete_doctrl_type_checks`, `specs/audits/SA-007/semgrep.yml#spec-gap-SA-007-G08-no-concrete-classifier-branches` | Classifier architecture is both behavior and code-structure critical |

## Phase 4 Evidence

- Failing tests command: `uv run pytest -q tests/core/test_sa007_spec_gaps.py`
  - Result: 9 failed (expected failing gate for SA-007 gaps)
  - Failing tests cover G01-G08 directly.
- Semgrep command: `uv run semgrep --config specs/audits/SA-007/semgrep.yml /Users/s22625/repos/doeff`
  - Result: 7 findings (expected violations for G03/G04/G07/G08)
- Regression baseline command: `uv run pytest -q tests/core/test_sa003_spec_gaps.py`
  - Result: 5 passed (no regression introduced by Phase 4 artifacts)

## Phase 5-6 Evidence

- Fixes applied for G01-G08 in:
  - `doeff/rust_vm.py`
  - `doeff/program.py`
  - `packages/doeff-vm/src/pyvm.rs`
  - `packages/doeff-vm/src/do_ctrl.rs`
  - `packages/doeff-vm/src/vm.rs`
  - `packages/doeff-vm/src/value.rs`
  - `packages/doeff-vm/src/continuation.rs`
- TDD verification:
  - `uv run pytest -q tests/core/test_sa007_spec_gaps.py` -> 9 passed
  - `uv run semgrep --config specs/audits/SA-007/semgrep.yml /Users/s22625/repos/doeff` -> 0 findings
- Phase 6 semantic review (Oracle): PASS (all G01-G08 marked CORRECT)

## Phase 7 Verification

- SA-007 verification gates:
  - `uv run pytest -q tests/core/test_sa007_spec_gaps.py` -> pass
  - `uv run semgrep --config specs/audits/SA-007/semgrep.yml /Users/s22625/repos/doeff` -> pass
- Repo-wide checks (informational):
- `uv run pytest -q` -> fails in pre-existing legacy runtime test modules (removed runtime import path errors; note: legacy interpreter has since been removed)
  - `uv run semgrep --config .semgrep.yaml /Users/s22625/repos/doeff` -> pre-existing unrelated findings outside SA-007 scope
