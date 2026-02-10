# SA-008 Progress

Date: 2026-02-08
Session: SA-008

Specs:
- `specs/vm/SPEC-008-rust-vm.md`
- `specs/vm/SPEC-009-rust-vm-migration.md`
- `specs/core/SPEC-TYPES-001-program-effect-separation.md`

## Status Table

| ID | Status | Test/Rule | Fix PR |
|---|---|---|---|
| SA-008-C01 | resolved-fix-spec |  |  |
| SA-008-C02 | resolved-fix-spec |  |  |
| SA-008-G01 | reviewed-correct | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G01_no_yielded_unknown_variant`, `specs/audits/SA-008/semgrep.yml#spec-gap-SA-008-G01-no-yielded-unknown` |  |
| SA-008-G02 | reviewed-correct | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G02_classifier_no_unknown_fallback_branch`, `specs/audits/SA-008/semgrep.yml#spec-gap-SA-008-G02-no-classifier-fallback` |  |
| SA-008-G03 | reviewed-correct | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G03_no_dothunk_export_alias`, `specs/audits/SA-008/semgrep.yml#spec-gap-SA-008-G03-no-dothunk-export` |  |
| SA-008-G04 | reviewed-correct | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G04_map_flatmap_not_generator_wrapped` |  |
| SA-008-G05 | reviewed-correct | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G05_map_flatmap_runtime_not_unimplemented` |  |
| SA-008-G06 | reviewed-correct | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G06_standard_effect_parse_not_marker_based`, `specs/audits/SA-008/semgrep.yml#spec-gap-SA-008-G06-no-getattr-standard-effect-parse` |  |
| SA-008-G07 | reviewed-correct | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G07_scheduler_parse_not_marker_based`, `specs/audits/SA-008/semgrep.yml#spec-gap-SA-008-G07-no-getattr-scheduler-effect-parse` |  |
| SA-008-G08 | reviewed-correct | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G08_kpc_parse_not_shape_attribute_driven` |  |
| SA-008-G09 | reviewed-correct | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G09_runresult_surface_unified` |  |
| SA-008-G10 | reviewed-correct | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G10_unhandled_effect_raises_clear_python_exception` |  |
| SA-008-G11 | reviewed-correct | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G11_no_public_runtime_internal_export`, `specs/audits/SA-008/semgrep.yml#spec-gap-SA-008-G11-no-public-runtime-internal-export` |  |
| SA-008-Q01 | resolved-remove-from-code |  |  |
| SA-008-Q02 | resolved-fix-both |  |  |
| SA-008-Q03 | resolved-fix-both |  |  |

## Dependency/Status Ledger

- fact: DoThunk elimination direction conflicts with DoThunk-centric test requirement language -> issue: SA-008-C01 -> auto-resolve/discussion: discussion-required -> action: fix-spec -> dependencies: []
- fact: classifier sections mix strict binary and unknown/fallback semantics -> issue: SA-008-C02 -> auto-resolve/discussion: discussion-required -> action: fix-spec -> dependencies: []
- fact: runtime retains Yielded::Unknown variant -> issue: SA-008-G01 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-008-C02]
- fact: classify_yielded remains concrete/fallback-based -> issue: SA-008-G02 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-008-C02, SA-008-G01]
- fact: DoThunk alias still exported -> issue: SA-008-G03 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-008-C01]
- fact: composition remains generator-backed in Program layer -> issue: SA-008-G04 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-008-C01, SA-008-G03]
- fact: DoCtrl Map/FlatMap runtime semantics not implemented -> issue: SA-008-G05 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-008-G04]
- fact: standard effect parsing remains marker/getattr-based -> issue: SA-008-G06 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-008-C02]
- fact: scheduler effect parsing remains marker/getattr-based -> issue: SA-008-G07 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-008-C02, SA-008-G06]
- fact: KPC parse/unwrap strategy remains shape/attribute-heavy -> issue: SA-008-G08 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-008-G02, SA-008-G04]
- fact: RunResult surface split persists -> issue: SA-008-G09 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: unhandled-effect error surface remains generic -> issue: SA-008-G10 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: extension runtime internals remain import-discoverable -> issue: SA-008-G11 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: DoThunk compatibility policy unresolved -> issue: SA-008-Q01 -> auto-resolve/discussion: discussion-required -> action: remove-from-code -> dependencies: [SA-008-C01]
- fact: unknown fallback policy unresolved -> issue: SA-008-Q02 -> auto-resolve/discussion: discussion-required -> action: fix-both -> dependencies: [SA-008-C02]
- fact: strict boundary vs ergonomic wrapper policy unresolved -> issue: SA-008-Q03 -> auto-resolve/discussion: discussion-required -> action: fix-both -> dependencies: []

## Phase 3 TDD Plan

| ID | Enforcement | Planned artifact | Why |
|---|---|---|---|
| SA-008-G01 | test + semgrep | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G01_no_yielded_unknown_variant`, `specs/audits/SA-008/semgrep.yml#spec-gap-SA-008-G01-no-yielded-unknown` | Strict binary classifier must eliminate unknown category |
| SA-008-G02 | test + semgrep | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G02_classifier_no_unknown_fallback_branch`, `specs/audits/SA-008/semgrep.yml#spec-gap-SA-008-G02-no-classifier-fallback` | No fallback/duck path in classify layer |
| SA-008-G03 | test + semgrep | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G03_no_dothunk_export_alias`, `specs/audits/SA-008/semgrep.yml#spec-gap-SA-008-G03-no-dothunk-export` | Public API must not expose DoThunk alias |
| SA-008-G04 | test | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G04_map_flatmap_not_generator_wrapped` | Composition must not rely on generator-wrapping workaround |
| SA-008-G05 | test | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G05_map_flatmap_runtime_not_unimplemented` | DoCtrl Map/FlatMap must execute, not raise unimplemented runtime path |
| SA-008-G06 | semgrep + test | `specs/audits/SA-008/semgrep.yml#spec-gap-SA-008-G06-no-getattr-standard-effect-parse`, `tests/core/test_sa008_spec_gaps.py::test_SA_008_G06_standard_effect_parse_not_marker_based` | Standard effect parsing should not use marker/getattr shortcuts |
| SA-008-G07 | semgrep + test | `specs/audits/SA-008/semgrep.yml#spec-gap-SA-008-G07-no-getattr-scheduler-effect-parse`, `tests/core/test_sa008_spec_gaps.py::test_SA_008_G07_scheduler_parse_not_marker_based` | Scheduler effect parsing should not use marker/getattr shortcuts |
| SA-008-G08 | test | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G08_kpc_parse_not_shape_attribute_driven` | KPC handling should be strict typed boundary, no attribute probing |
| SA-008-G09 | test | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G09_runresult_surface_unified` | Public RunResult behavior must be unified |
| SA-008-G10 | test | `tests/core/test_sa008_spec_gaps.py::test_SA_008_G10_unhandled_effect_raises_clear_python_exception` | Fail hard with clear Python exception on unhandled effect |
| SA-008-G11 | semgrep | `specs/audits/SA-008/semgrep.yml#spec-gap-SA-008-G11-no-public-runtime-internal-export` | Runtime internals should not be public-import discoverable |

## Phase 4 Evidence

- Failing test command: `uv run pytest -q tests/core/test_sa008_spec_gaps.py`
  - Result: `10 failed, 1 passed` (expected failing gate)
- Semgrep command: `uv run semgrep --config specs/audits/SA-008/semgrep.yml /Users/s22625/repos/doeff`
  - Result: `57 findings` (expected violations for structural gaps)
- Regression baseline command: `uv run pytest -q tests/core/test_sa007_spec_gaps.py`
  - Result: `9 passed` (no regression from SA-008 phase-4 artifact creation)

## Phase 5-6 Evidence

- Fix clusters applied using parallel subagents:
  - Cluster A/D: `yielded.rs`, `pyvm.rs`, `vm.rs`, `doeff_vm/__init__.py`, `doeff/rust_vm.py`
  - Cluster B/C/G: `doeff/program.py`, `handler.rs`, `scheduler.rs`, `pyvm.rs`, `vm.rs`, `doeff_vm/__init__.py`
- Rebuild command: `uv run maturin develop` (in `packages/doeff-vm`)
- Post-fix checks:
  - `uv run pytest -q tests/core/test_sa008_spec_gaps.py` -> `11 passed`
  - `uv run semgrep --config specs/audits/SA-008/semgrep.yml /Users/s22625/repos/doeff` -> `0 findings`
  - `uv run pytest -q tests/core/test_sa007_spec_gaps.py` -> `9 passed`
- Semantic review (Phase 6): final review returned all G01-G11 = `CORRECT`.

## Phase 7 Verification

- SA-008 verification gates:
  - `uv run pytest -q tests/core/test_sa008_spec_gaps.py` -> pass
  - `uv run semgrep --config specs/audits/SA-008/semgrep.yml /Users/s22625/repos/doeff` -> pass
- Additional regression check:
  - `uv run pytest -q tests/core/test_sa007_spec_gaps.py` -> pass
- Repository-wide checks (informational):
  - `uv run pytest -q` -> fails during collection due pre-existing removed CESK module imports and one public-api DoThunk import expectation not aligned with SA-008 policy decision.
  - `uv run semgrep --config .semgrep.yaml /Users/s22625/repos/doeff` -> existing unrelated policy findings in docs/examples/other packages.
