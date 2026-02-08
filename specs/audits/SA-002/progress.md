# SA-002 Progress

## Phase Status

| Phase | Status | Notes |
|---|---|---|
| Phase 0: Session Initialization | done | `SA-002` created, registry updated |
| Phase 1: Parallel Section Review | done | 10 review units completed + cross-reference dedupe |
| Phase 2: Gap Classification + Report | done | Report and resolutions created |
| Phase 3: TDD Plan | done | Gap-by-gap enforcement plan defined below |
| Phase 4: Write Failing Tests + Semgrep Rules | done | Failing tests and semgrep violations confirmed; regression check green |
| Phase 5: Apply Fixes | done | All SA-002 gaps fixed and revalidated by SA-002 tests/semgrep |
| Phase 6: Semantic Review | done | Round-4 review marks G01..G09 all CORRECT |
| Phase 7: Verify and Commit | done-with-preexisting-failures | SA-002 checks green; repo-wide pytest/semgrep have pre-existing unrelated failures |

## Item Tracker

| ID | Category | Severity | Enforcement | Status | Test/Rule | Notes |
|---|---|---|---|---|---|---|
| SA-002-C01 | Contradiction | n/a | - | resolved-fix-spec |  | Mandatory KPC effect-dispatch path selected |
| SA-002-G01 | Gap | Critical | test + semgrep | reviewed-CORRECT | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G01_classifier_no_fallback_introspection`; `spec-gap-SA-002-G01-no-fallback-introspection` | Phase 6 round-2 CORRECT |
| SA-002-G02 | Gap | Critical | test + semgrep | reviewed-CORRECT | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G02_no_python_kpc_transitional_state`; `spec-gap-SA-002-G02-no-python-kpc-class` | Phase 6 round-4 CORRECT |
| SA-002-G03 | Gap | Moderate | test | reviewed-CORRECT | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G03_no_implicit_kpc_install_in_vm_new` | Phase 6 round-2 CORRECT |
| SA-002-G04 | Gap | Moderate | test | reviewed-CORRECT | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G04_runresult_protocol_has_required_members` | Phase 6 round-2 CORRECT |
| SA-002-G05 | Gap | Moderate | test | reviewed-CORRECT | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G05_default_handlers_and_presets_contract` | Phase 6 round-2 CORRECT |
| SA-002-G06 | Gap | Moderate | test | reviewed-CORRECT | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G06_scheduler_wake_waiters_has_resume_path` | Phase 6 round-2 CORRECT after wake queue fix |
| SA-002-G07 | Gap | Moderate | test + semgrep | reviewed-CORRECT | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G07_doctrl_pyclasses_extend_base`; `spec-gap-SA-002-G07-doctrl-extends-base` | Phase 6 round-2 CORRECT |
| SA-002-G08 | Gap | Minor | semgrep | reviewed-CORRECT | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G08_expected_vm_module_split_files_exist`; `spec-gap-SA-002-G08-consolidated-modules-forbidden` | Phase 6 round-4 CORRECT |
| SA-002-G09 | Gap | Minor | test | reviewed-CORRECT | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G09_to_generator_strict_not_callable_fallback` | Phase 6 round-3 CORRECT |
| SA-002-Q01 | Discussion | n/a | - | resolved-remove-from-code |  | Remove unspecified fallback behavior |
| SA-002-Q02 | Discussion | n/a | - | resolved-fix-both |  | `doeff_vm` is internal-only API |
| SA-002-Q03 | Discussion | n/a | - | resolved-fix-both |  | Eliminate transitional hybrid KPC/primitive paths |

## Phase 3 TDD Plan (Gap -> Enforcement)

| Gap ID | Planned test | Planned semgrep | Rationale |
|---|---|---|---|
| SA-002-G01 | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G01_classifier_no_fallback_introspection` | `specs/audits/SA-002/semgrep/rules.yml#spec-gap-SA-002-G01-no-fallback-introspection` | Behavioral + architecture: classifier contract plus no import/getattr fallback |
| SA-002-G02 | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G02_no_python_kpc_transitional_state` | `specs/audits/SA-002/semgrep/rules.yml#spec-gap-SA-002-G02-no-python-kpc-class` | Ensure Rust-side KPC-only direction and prevent hybrid fallback |
| SA-002-G03 | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G03_no_implicit_kpc_install_in_vm_new` | - | Behavioral check for explicit KPC handler install only |
| SA-002-G04 | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G04_runresult_protocol_has_required_members` | - | Public contract check for RunResult protocol surface |
| SA-002-G05 | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G05_default_handlers_and_presets_contract` | - | Preset/default contract verification |
| SA-002-G06 | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G06_scheduler_wake_waiters_has_resume_path` | - | Behavioral scheduler correctness |
| SA-002-G07 | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G07_doctrl_pyclasses_extend_base` | `specs/audits/SA-002/semgrep/rules.yml#spec-gap-SA-002-G07-doctrl-extends-base` | Structural inheritance constraint |
| SA-002-G08 | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G08_expected_vm_module_split_files_exist` | `specs/audits/SA-002/semgrep/rules.yml#spec-gap-SA-002-G08-consolidated-modules-forbidden` | Enforce module structure objective |
| SA-002-G09 | `tests/core/test_sa002_spec_gaps.py::test_SA_002_G09_to_generator_strict_not_callable_fallback` | - | Strict input validation semantics |

## Phase 4 Evidence

- Pytest failing-gap run: `uv run pytest tests/core/test_sa002_spec_gaps.py`
  - Result: `9 failed` (all expected SA-002 gap-detection tests)
- Semgrep failing-rule run: `semgrep --config specs/audits/SA-002/semgrep/rules.yml doeff/ packages/`
  - Result: `21 findings` across SA-002 rules
- Regression check: `uv run pytest tests/core/test_sa001_spec_gaps.py`
  - Result: `30 passed`

Phase 4 completion gate status:
- [x] Every gap has failing test or semgrep rule (or both)
- [x] Failing tests executed and confirmed failing
- [x] Semgrep rules executed and confirmed violations
- [x] Regression check passed (baseline SA-001 suite)
- [x] Gap -> test/rule mapping recorded

## Phase 5-6 Evidence (Fix/Review Loop)

- Fix verification run: `make test-spec-audit-sa002`
  - Result: `9 passed` (SA-002 tests), semgrep `0 findings`
- Regression run: `uv run pytest tests/core/test_sa001_spec_gaps.py`
  - Result: `30 passed`
- Semantic review rounds:
- Round 1: G06=HACK, G02/G09=INCOMPLETE, G08=HACK
- Round 2: G06 resolved CORRECT; G02=HACK, G08=INCOMPLETE, G09=INCOMPLETE
- Round 3: G09 resolved CORRECT; G02=HACK, G08=INCOMPLETE
- Round 4: G02 resolved CORRECT; G08 resolved CORRECT

## Phase 7 Evidence

- SA-002 verification target: `make test-spec-audit-sa002`
  - Result: `9 passed` + SA-002 semgrep `0 findings`
- SA-001 regression: `uv run pytest tests/core/test_sa001_spec_gaps.py`
  - Result: `30 passed`
- Full repository pytest: `uv run pytest`
  - Result: interrupted at collection with `13` pre-existing CESK import errors (`doeff.cesk.*` modules missing)
- Repository semgrep: `semgrep --config .semgrep.yaml doeff/ packages/ --error`
  - Result: `61` findings, pre-existing and outside SA-002 scope

Conclusion:
- SA-002 goals are complete (all G01..G09 reviewed CORRECT).
- Repo-wide verification has unrelated pre-existing failures.
