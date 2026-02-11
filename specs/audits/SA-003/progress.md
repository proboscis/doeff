# SA-003 Progress

## Phase Status

| Phase | Status | Notes |
|---|---|---|
| Phase 0: Session Initialization | done | `SA-003` created, registry updated |
| Phase 1: Parallel Section Review | done | 9 review units (U1-U9) completed and deduplicated |
| Phase 2: Gap Classification + Report | done | `report.md`, `resolutions.md`, `progress.md` created |
| Phase 3: TDD Plan | done | Resolution profile applied from SA-003 defaults and discussion outcomes |
| Phase 4: Write Failing Tests + Semgrep Rules | done | Failing tests and semgrep violations confirmed |
| Phase 5: Apply Fixes | done | Code/spec fixes applied per SA-003 resolutions |
| Phase 6: Semantic Review | done | Review agents marked G01/G02/G03/G04/G06 as CORRECT |
| Phase 7: Verify and Commit | done-with-preexisting-failures | SA-003 checks green; repo-wide pytest/semgrep have pre-existing failures |

## Item Tracker

| ID | Category | Severity | Enforcement | Status | Notes |
|---|---|---|---|---|---|
| SA-003-C01 | Contradiction | n/a | spec | resolved-fix-spec | Spec wording update required |
| SA-003-G01 | Gap | Critical | test | reviewed-CORRECT | `tests/core/test_sa003_spec_gaps.py::test_SA_003_G01_callfunc_path_has_distinct_pending_state` |
| SA-003-G02 | Gap | Critical | test + semgrep | reviewed-CORRECT | `tests/core/test_sa003_spec_gaps.py::test_SA_003_G02_python_call_errors_are_normalized_to_generror`; `spec-gap-SA-003-G02-startprogram-no-question-mark` |
| SA-003-G03 | Gap | Moderate | test + semgrep | reviewed-CORRECT | `tests/core/test_sa003_spec_gaps.py::test_SA_003_G03_isolated_spawn_without_snapshot_must_throw`; `spec-gap-SA-003-G03-no-isolated-to-shared-fallback` |
| SA-003-G04 | Gap | Moderate | test + semgrep | reviewed-CORRECT | `tests/core/test_sa003_spec_gaps.py::test_SA_003_G04_get_handlers_must_not_skip_missing_entries`; `spec-gap-SA-003-G04-get-handlers-no-filter-map` |
| SA-003-G05 | Gap | Moderate | spec | resolved-fix-spec | Keep compatibility path, document explicitly |
| SA-003-G06 | Gap | Moderate | test | reviewed-CORRECT | `tests/core/test_sa003_spec_gaps.py::test_SA_003_G06_python_async_syntax_escape_alias_exported` (canonical export, no fallback alias) |
| SA-003-G07 | Gap | Minor | spec | resolved-fix-spec | Update crate structure section |
| SA-003-G08 | Gap | Minor | spec | resolved-fix-spec | Update GIL invariant wording |
| SA-003-Q01 | Discussion | n/a | spec | resolved-skip | Already specified in strict spec: PyVM is not public API |
| SA-003-Q02 | Discussion | n/a | code | resolved-skip | Enforced DoExpr contract in `doeff/rust_vm.py`; non-DoExpr rejected |
| SA-003-Q03 | Discussion | n/a | spec | resolved-skip | Callback mechanism is internal-only, not user-facing API |
| SA-003-Q04 | Discussion | n/a | code | resolved-fix-code | Removed getattr-based metadata extraction from classify path |

## Phase 3 TDD Plan (Gap -> Enforcement)

| Gap ID | Planned test | Planned semgrep | Rationale |
|---|---|---|---|
| SA-003-G01 | `tests/core/test_sa003_spec_gaps.py::test_SA_003_G01_callfunc_path_has_distinct_pending_state` | - | Prevent StartProgramFrame misuse on CallFunc path |
| SA-003-G02 | `tests/core/test_sa003_spec_gaps.py::test_SA_003_G02_python_call_errors_are_normalized_to_generror` | `spec-gap-SA-003-G02-startprogram-no-question-mark` | Enforce PyErr->GenError normalization |
| SA-003-G03 | `tests/core/test_sa003_spec_gaps.py::test_SA_003_G03_isolated_spawn_without_snapshot_must_throw` | `spec-gap-SA-003-G03-no-isolated-to-shared-fallback` | Block silent semantics downgrade |
| SA-003-G04 | `tests/core/test_sa003_spec_gaps.py::test_SA_003_G04_get_handlers_must_not_skip_missing_entries` | `spec-gap-SA-003-G04-get-handlers-no-filter-map` | Ensure missing handler mapping fails fast |
| SA-003-G06 | `tests/core/test_sa003_spec_gaps.py::test_SA_003_G06_python_async_syntax_escape_alias_exported` | - | Align user-visible API naming |

Spec-only items to apply in Phase 5 docs/spec updates: `SA-003-C01`, `SA-003-G05`, `SA-003-G07`, `SA-003-G08`.

## Phase 4 Evidence

- Pytest failing-gap run: `uv run pytest tests/core/test_sa003_spec_gaps.py`
  - Result (before fixes): `5 failed` (expected SA-003 gap-detection failures)
- Semgrep failing-rule run: `semgrep --config specs/audits/SA-003/semgrep/rules.yml doeff/ packages/`
  - Result (before fixes): findings for SA-003-G02/G03/G04 (expected); G01 structural rule was removed due over-broad matching after code fix, covered by targeted test instead

## Phase 5-6 Evidence

- Fix verification run: `uv run pytest tests/core/test_sa003_spec_gaps.py`
  - Result: `5 passed`
- SA-003 semgrep re-run: `semgrep --config specs/audits/SA-003/semgrep/rules.yml doeff/ packages/`
  - Result: `0 findings`
- Semantic review agent verdicts:
  - G01: CORRECT (`bg_edc1f377`)
  - G02: CORRECT (`bg_9d2a6f35`)
  - G03: CORRECT (`bg_22209968`)
  - G04: CORRECT (`bg_a65b9574`)
  - G06: CORRECT (`bg_9f1236ea`)

## Phase 7 Evidence

- SA-003 test suite: `uv run pytest tests/core/test_sa003_spec_gaps.py` -> `5 passed`
- SA-003 semgrep: `semgrep --config specs/audits/SA-003/semgrep/rules.yml doeff/ packages/` -> `0 findings`
- Regression suites:
  - `uv run pytest tests/core/test_sa001_spec_gaps.py` -> `30 passed`
  - `uv run pytest tests/core/test_spec_gaps.py` -> `22 passed`
- Full repository checks:
  - `uv run pytest` -> interrupted at collection with `13` pre-existing legacy runtime import errors (note: legacy interpreter has since been removed)
  - `semgrep --config .semgrep.yaml doeff/ packages/ --error` -> `61` pre-existing findings outside SA-003 scope

## Q Resolution Conformance Evidence (strict mode)

- Q02 code path (`doeff/rust_vm.py`): run/async_run normalization enforces DoExpr semantics
  - accepts `to_generator` programs directly
  - accepts `EffectBase` DoExpr values via one-step wrapper
  - rejects non-DoExpr objects with `TypeError`
- Q04 code path (`packages/doeff-vm/src/pyvm.rs`): classify path removed `extract_call_metadata*`
  and no longer performs `getattr("function_name")`/`getattr("kleisli_source")` metadata extraction.
- Verification:
  - `uv run pytest tests/core/test_rust_vm_api_strict.py` -> `5 passed`
  - `uv run pytest tests/core/test_sa003_spec_gaps.py` -> `5 passed`
