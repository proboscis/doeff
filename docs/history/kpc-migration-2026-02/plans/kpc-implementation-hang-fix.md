# KPC Macro Implementation + Hanging Test Remediation

## TL;DR

> **Quick Summary**: Implement the new KPC macro model end-to-end (Python API + Rust VM + tests) and resolve the doeff-13 hanging-path risk with deterministic, time-bounded TDD regression coverage.
>
> **Deliverables**:
> - Python layer migrated off KPC handler semantics (`__call__`/defaults/presets/exports)
> - Rust VM KPC effect-handler wiring removed/superseded from runtime paths
> - Public/spec tests updated from old KPC-as-effect expectations to macro model
> - Hanging-path regression tests added with bounded execution and non-hanging guarantees
> - Full targeted + broad pytest verification green
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: Task 1 -> Task 2 -> Task 4 -> Task 7 -> Task 10 -> Task 12

---

## Context

### Original Request

User requested planning for implementation after spec updates, with explicit intent to fix the hanging test issue as part of the same effort.

### Interview Summary

**Key Discussions**:
- Scope confirmed: **single combined plan** for full KPC implementation + hanging test fix.
- Test strategy confirmed: **TDD** (RED-GREEN-REFACTOR).
- Test infra confirmed present: pytest + pytest-asyncio strict mode.
- Compatibility policy confirmed: **hard break now** (no temporary KPC-as-effect shim).

**Research Findings**:
- Specs now define KPC as macro (`KleisliProgram.__call__()` returns `Call` DoCtrl directly).
- Current code still encodes old KPC-as-effect model and KPC handler plumbing.
- A doeff-13 skip marker indicates known incompatibility/hang-risk path around `@do` handlers.
- Timeout/hang protections are mostly ad-hoc (no suite-level timeout plugin/config policy).

### Metis Review

**Identified Gaps (addressed in this plan)**:
- Compatibility policy ambiguity (hard break vs transitional shim) requires explicit decision.
- Python/Rust VM divergence risk mitigated with parity checkpoints.
- Hanging-fix "done" criteria formalized as time-bounded automated checks.
- Scope creep locked down (no broad runtime refactor/perf redesign).

---

## Work Objectives

### Core Objective

Align runtime implementation with the new KPC macro specs while eliminating hanging behavior in the known doeff-13 path through deterministic regression tests and bounded execution guarantees.

### Concrete Deliverables

1. Python API/runtime updated to macro semantics:
   - `doeff/kleisli.py`
   - `doeff/program.py`
   - `doeff/rust_vm.py`
   - `doeff/handlers.py`
   - `doeff/presets.py`
2. Rust VM runtime path updated:
   - `packages/doeff-vm/src/pyvm.rs`
   - `packages/doeff-vm/src/handler.rs`
   - `packages/doeff-vm/src/effect.rs`
   - `packages/doeff-vm/src/lib.rs`
3. Test suite updates and additions:
   - `tests/public_api/test_types_001_kpc.py`
   - `tests/public_api/test_types_001_hierarchy.py`
   - `tests/public_api/test_types_001_handler_protocol.py`
   - `tests/public_api/test_doeff13_hang_regression.py`
   - `tests/public_api/test_kpc_macro_runtime_contract.py`
   - `tests/core/test_sa001_spec_gaps.py`
   - `tests/core/test_do_methods.py`
   - `tests/core/test_doexpr_hierarchy.py`
   - deterministic hang-regression tests at `tests/public_api/test_doeff13_hang_regression.py`

### Definition of Done

- [x] `KleisliProgram.__call__()` returns macro `Call` DoCtrl semantics (not KPC effect object)
- [x] `default_handlers()` no longer includes `kpc`
- [x] presets no longer include `kpc`
- [x] runtime no longer requires/uses KPC handler dispatch for `@do` call path
- [x] hanging-path regression test(s) pass under explicit bounded runtime
- [x] targeted spec/public-api test groups pass
- [x] full `uv run pytest` passes without introducing new broad skips/xfails

### Must Have

- TDD-first workflow for migration and hang fix
- explicit parity checks between Python surface behavior and VM runtime behavior
- deterministic, automatable hang detection and failure criteria
- focused scope: only KPC macro migration + hanging path remediation

### Must NOT Have (Guardrails)

- no unrelated architecture refactors beyond required migration edits
- no broad timeout framework rollout unless directly needed to validate hang fix
- no silent compatibility assumptions; compatibility decision must be recorded
- no manual-only verification steps
- no edits to unrelated user changes in dirty worktree
- no temporary compatibility shim for old KPC-as-effect behavior

### Deferred/Out of Scope

- non-KPC runtime performance optimization unrelated to hang fix
- broad scheduler redesign unrelated to doeff-13 path
- documentation sweep beyond minimal notes required by changed tests/API behavior

---

## Verification Strategy (MANDATORY)

### Test Decision

- **Infrastructure exists**: YES
- **User wants tests**: YES (TDD)
- **Framework**: pytest + pytest-asyncio (strict)

### TDD Workflow (applies to all implementation tasks)

For each task:
1. **RED**: add/adjust failing test(s) for the target behavior
2. **GREEN**: minimal code changes to pass
3. **REFACTOR**: cleanup while preserving green

### Hang-Fix Verification Policy

- Every hanging-path test must have explicit bounded execution criteria.
- Use command-level timeout wrappers in verification tasks where needed.
- Record exact command + expected bounded completion as acceptance evidence.

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundational lock-in)
  Task 1  Compatibility decision + baseline repro map

Wave 1b (RED tests in parallel after Task 1)
  Task 2  RED tests for macro semantics/public API
  Task 2b RED tests for runtime contract invariants
  Task 3  RED tests for hang path (deterministic + bounded)

Wave 2 (Core implementation tracks in parallel)
  Task 4  Python layer migration (kleisli/program/rust_vm defaults/presets/exports)
  Task 5  Rust VM migration (pyvm/handler/effect/lib KPC wiring)

Wave 3 (Convergence)
  Task 6  Public/spec test migration alignment
  Task 7  Hang-path remediation implementation
  Task 8  Parity and negative-path tests

Wave 4 (Final verification + stabilization)
  Task 9  Targeted suites + bounded hang checks
  Task 10 Full suite verification and flake/hang guard checks
  Task 11 Final cleanup and commit grouping prep
  Task 12 Release-ready validation pass
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 2,2b,3,4,5,6,7 | none |
| 2 | 1 | 4,6 | 2b,3 |
| 2b | 1 | 5,8,9 | 2,3 |
| 3 | 1 | 7,9 | 2,2b |
| 4 | 1,2 | 8,9 | 5 |
| 5 | 1,2,2b | 6,7,8,9 | 4 |
| 6 | 2,4,5 | 9,10 | 7 |
| 7 | 3,4,5 | 9,10 | 6 |
| 8 | 4,5,2b | 9,10 | 6,7 |
| 9 | 3,4,5,6,7,8,2b | 10,12 | none |
| 10 | 9 | 12 | 11 |
| 11 | 9 | 12 | 10 |
| 12 | 10,11 | none | none |

### Agent Dispatch Summary

| Wave | Tasks | Recommended Agents |
|------|-------|-------------------|
| 1 | 1 | sequential starter task |
| 1b | 2,2b,3 | `delegate_task(category="unspecified-high", load_skills=["python-coding-style"], run_in_background=true)` |
| 2 | 4,5 | two parallel `unspecified-high` workers |
| 3 | 6,7,8 | three parallel workers after Wave 2 |
| 4 | 9,10,11,12 | one verification-focused + one cleanup-focused worker |

---

## TODOs

- [x] 1. Lock compatibility policy and baseline reproduction

  **What to do**:
  - Record compatibility policy for old KPC-as-effect behavior:
    - hard break immediately (no shim/deprecation window in this plan)
  - Reproduce current hanging/failure behavior with exact minimal command(s).
  - Capture baseline outputs and failing assertions in:
    - `.sisyphus/evidence/kpc-hang-baseline.md`

  **Must NOT do**:
  - no implementation edits in this task
  - no changing unrelated tests

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 1 (sequential starter)
  - **Blocks**: 4,5,6,7
  - **Blocked By**: None

  **References**:
  - `tests/public_api/test_types_001_handler_protocol.py:389` - doeff-13 skip marker and likely hang path clue
  - `tests/public_api/test_types_001_kpc.py:1` - old KPC semantics currently encoded
  - `.sisyphus/plans/kpc-spec-extraction.md:76` - completed spec cleanup definition of done
  - `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md` - target semantics

  **Acceptance Criteria**:
  - [x] Compatibility policy chosen and recorded in execution notes
  - [x] Baseline failing/hanging command(s) documented with reproducible invocation
  - [x] `.sisyphus/evidence/kpc-hang-baseline.md` exists and includes command + observed outcome

- [x] 2. RED: public API semantics tests for macro model

  **What to do**:
  - Update/add failing tests asserting:
    - `@do` call returns macro control object semantics (not legacy KPC effect semantics)
    - no dependency on KPC handler presence
  - Focus first on public API tests before code edits.

  **Must NOT do**:
  - no production code changes in RED step

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 4,6
  - **Blocked By**: 1

  **References**:
  - `tests/public_api/test_types_001_kpc.py` - existing KD tests to migrate
  - `tests/public_api/test_types_001_hierarchy.py` - hierarchy assertions currently old-model
  - `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md:17` - macro semantics overview

  **Acceptance Criteria**:
  - [x] RED tests fail for expected macro-model assertions before implementation
  - [x] Failure output references target assertions (not unrelated failures)

- [x] 2b. RED: create runtime contract test file for macro-model invariants

  **What to do**:
  - Create `tests/public_api/test_kpc_macro_runtime_contract.py` with failing assertions for:
    - `default_handlers()` excludes `kpc`
    - presets exclude `kpc`
    - old KPC-effect entry assumptions are absent/fail-fast per hard-break policy

  **Must NOT do**:
  - no production code changes in RED step

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1b
  - **Blocks**: 5,8,9
  - **Blocked By**: 1

  **References**:
  - `doeff/rust_vm.py:131`
  - `doeff/handlers.py:3`
  - `doeff/presets.py:13`
  - `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md:31`

  **Acceptance Criteria**:
  ```bash
  uv run pytest tests/public_api/test_kpc_macro_runtime_contract.py -q
  ```
  - [x] RED failure is produced before implementation changes

- [x] 3. RED: hanging-path deterministic regression tests

  **What to do**:
  - Add deterministic hang-regression RED tests in `tests/public_api/test_doeff13_hang_regression.py`.
  - Use thread-based watchdog assertion (same style as `tests/effects/test_external_promise.py`) with explicit budget.
  - Budget for this plan: each hang-regression case must complete within **3.0s**.

  **Must NOT do**:
  - no flaky sleep-based assertion without bounded watchdog

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1b
  - **Blocks**: 7,9
  - **Blocked By**: 1

  **References**:
  - `tests/public_api/test_types_001_handler_protocol.py:389` - existing skip/known issue note
  - `tests/effects/test_external_promise.py:17` - thread timeout helper pattern (`thread.join(timeout=...)`)
  - `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md:269` - recursion rationale section

  **Acceptance Criteria**:
  - [x] RED test reproduces failure against old behavior in `tests/public_api/test_doeff13_hang_regression.py`
  - [x] RED command finishes within 3.0s (fails fast, does not hang indefinitely)
  - [x] Command:
    ```bash
    uv run python -c "import subprocess,sys; r=subprocess.run(['pytest','tests/public_api/test_doeff13_hang_regression.py','-q'], timeout=3.0); sys.exit(r.returncode)"
    ```

- [x] 4. GREEN: migrate Python call path and default handler surfaces

  **What to do**:
  - Align Python layer with macro spec:
    - `KleisliProgram.__call__` semantics in `doeff/kleisli.py`
    - KPC compatibility helpers in `doeff/program.py`
    - remove `kpc` from `default_handlers` in `doeff/rust_vm.py`
    - update `doeff/handlers.py` exports
    - update `doeff/presets.py` behavior

  **Must NOT do**:
  - do not alter unrelated effect semantics

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 5)
  - **Parallel Group**: Wave 2
  - **Blocks**: 6,7,8,9
  - **Blocked By**: 1,2

  **References**:
  - `doeff/kleisli.py:63` - current `__call__` returns `KleisliProgramCall`
  - `doeff/program.py:508` - `KleisliProgramCall` compatibility attachment points
  - `doeff/rust_vm.py:131` - current defaults include `kpc`
  - `doeff/handlers.py:3` - exported sentinel list includes `kpc`
  - `doeff/presets.py:26` - async preset composition

  **Acceptance Criteria**:
  - [x] Python API tests from Task 2 move from RED to GREEN
  - [x] `default_handlers` no longer includes `kpc`
  - [x] presets and exports reflect removed kpc handler surface

- [x] 5. GREEN: migrate Rust VM KPC-effect/handler runtime wiring

  **What to do**:
  - Remove or gate KPC-specific effect-handler wiring in runtime paths:
    - pyvm imports/wiring
    - handler factories/programs for KPC
    - `PyKPC` exposure path and related lib exports
  - Keep migration minimal and aligned with selected compatibility policy.
  - **Authoritative runtime path after this task**: macro-call path only; legacy KPC handler path is not a supported execution route.

  **Must NOT do**:
  - no broad VM refactor outside KPC-specific paths

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 4)
  - **Parallel Group**: Wave 2
  - **Blocks**: 6,7,8,9
  - **Blocked By**: 1,2,2b

  **References**:
  - `packages/doeff-vm/src/pyvm.rs:75` - imports include KPC handlers
  - `packages/doeff-vm/src/handler.rs:640` - KpcHandlerFactory/KpcHandlerProgram
  - `packages/doeff-vm/src/handler.rs:1605` - ConcurrentKpcHandler paths
  - `packages/doeff-vm/src/effect.rs:50` - `PyKPC` definition
  - `packages/doeff-vm/src/lib.rs` - exported KPC symbols

  **Acceptance Criteria**:
  - [x] No runtime dependency on KPC handler dispatch for normal `@do` call flow
  - [x] VM-focused tests updated/green for selected compatibility policy
  - [x] Command:
    ```bash
    uv run pytest tests/public_api/test_kpc_macro_runtime_contract.py -q
    uv run pytest tests/core/test_sa001_spec_gaps.py -q
    ```

- [x] 6. GREEN: migrate public/spec-facing tests to macro model

  **What to do**:
  - Update affected tests that currently encode old model behavior.
  - Ensure assertion language reflects macro semantics and new defaults.

  **Must NOT do**:
  - no blanket skips to force green

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 7)
  - **Parallel Group**: Wave 3
  - **Blocks**: 9,10
  - **Blocked By**: 2,4,5

  **References**:
  - `tests/public_api/test_types_001_kpc.py`
  - `tests/public_api/test_types_001_hierarchy.py`
  - `tests/core/test_sa001_spec_gaps.py`
  - `tests/core/test_do_methods.py`
  - `tests/core/test_doexpr_hierarchy.py`

  **Acceptance Criteria**:
  - [x] Targeted public/spec suites pass
  - [x] No stale assertions expecting KPC handler dependence remain

- [x] 7. GREEN: implement hanging-path remediation

  **What to do**:
  - Implement minimal fix for hanging recursion/re-entry path validated by Task 3 **on the post-Task-5 macro runtime path**.
  - Preserve expected handler protocol behavior outside targeted path.
  - Do NOT revive/patch legacy KPC-handler dispatch flow as remediation strategy.
  - Apply changes in concrete runtime touchpoints first:
    - `packages/doeff-vm/src/handler.rs` (`parse_kpc_python_effect`, `extract_kpc_arg`, `is_do_expr_candidate`, `KpcHandlerProgram` flow)
    - `packages/doeff-vm/src/vm.rs` (`visible_handlers`, `start_dispatch`, `handle_delegate`, `handle_handler_return`)

  **Must NOT do**:
  - no broad handler protocol redesign

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 6)
  - **Parallel Group**: Wave 3
  - **Blocks**: 9,10
  - **Blocked By**: 3,4,5

  **References**:
  - `tests/public_api/test_types_001_handler_protocol.py:389`
  - `packages/doeff-vm/src/handler.rs:473` - `parse_kpc_python_effect`
  - `packages/doeff-vm/src/handler.rs:626` - `extract_kpc_arg`
  - `packages/doeff-vm/src/handler.rs:633` - `is_do_expr_candidate`
  - `packages/doeff-vm/src/handler.rs:695` - `KpcHandlerProgram`
  - `packages/doeff-vm/src/vm.rs:1462` - `visible_handlers`
  - `packages/doeff-vm/src/vm.rs:1498` - `start_dispatch`
  - `packages/doeff-vm/src/vm.rs:1824` - `handle_delegate`
  - `packages/doeff-vm/src/vm.rs:1928` - `handle_handler_return`
  - macro recursion rationale in `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md:275`

  **Acceptance Criteria**:
  - [x] Hang regression test completes and passes under 3.0s bounded timeout
  - [x] No new deadlocks/hangs introduced in nearby handler tests
  - [x] Command:
    ```bash
    uv run python -c "import subprocess,sys; r=subprocess.run(['pytest','tests/public_api/test_doeff13_hang_regression.py','-q'], timeout=3.0); sys.exit(r.returncode)"
    ```

- [x] 8. REFACTOR: parity + negative-path assertions

  **What to do**:
  - Add parity assertions between Python and VM paths where applicable.
  - Add negative tests for removed old-model behavior per compatibility decision.

  **Must NOT do**:
  - no speculative behavior changes without spec reference

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: 9,10
  - **Blocked By**: 4,5,2b

  **References**:
  - `doeff/rust_vm.py`
  - `packages/doeff-vm/src/pyvm.rs`
  - affected tests from Tasks 6/7

  **Acceptance Criteria**:
  - [x] Explicit parity checks pass
  - [x] Negative-path behavior is tested and green
  - [x] Command:
    ```bash
    uv run pytest tests/public_api/test_kpc_macro_runtime_contract.py -q
    ```

- [x] 9. Targeted verification sweep (agent-executable)

  **What to do**:
  - Execute focused suites and capture outputs.
  - Confirm no regressions in KPC-related public/spec tests.

  **Required Evidence Artifacts**:
  - `.sisyphus/evidence/kpc-targeted-verification.txt`
  - `.sisyphus/evidence/kpc-negative-path-verification.txt`

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 4
  - **Blocks**: 10,12
  - **Blocked By**: 3,4,5,6,7,8,2b

  **Acceptance Criteria (automated commands)**:
  ```bash
  uv run pytest tests/public_api/test_types_001_kpc.py -q
  uv run pytest tests/public_api/test_types_001_hierarchy.py -q
  uv run pytest tests/public_api/test_types_001_handler_protocol.py -q
  uv run pytest tests/public_api/test_kpc_macro_runtime_contract.py -q
  uv run pytest tests/public_api/test_doeff13_hang_regression.py -q
  uv run pytest tests/core/test_sa001_spec_gaps.py tests/core/test_do_methods.py tests/core/test_doexpr_hierarchy.py -q
  uv run python -c "import doeff; hs=doeff.default_handlers(); names=[getattr(h,'name',repr(h)) for h in hs]; import sys; sys.exit(1 if any('kpc' in str(n).lower() for n in names) else 0)"
  ```
  - [x] all above commands exit 0
  - [x] evidence artifacts updated with command outputs

- [x] 10. Full-suite verification and bounded hang check

  **What to do**:
  - Run broader suite and ensure no hangs.
  - Ensure hang-related test path has explicit bounded runtime command evidence.

  **References**:
  - `tests/public_api/test_doeff13_hang_regression.py`
  - `tests/public_api/test_kpc_macro_runtime_contract.py`
  - `.sisyphus/evidence/kpc-targeted-verification.txt`

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 11)
  - **Parallel Group**: Wave 4
  - **Blocks**: 12
  - **Blocked By**: 9

  **Acceptance Criteria**:
  ```bash
  uv run python -c "import subprocess,sys; r=subprocess.run(['pytest','tests/public_api/test_doeff13_hang_regression.py','-q'], timeout=3.0); sys.exit(r.returncode)"
  uv run pytest -q
  ```
  - [x] exits 0
  - [x] no hang in doeff-13 regression path; each regression case finishes within 3.0s

- [x] 11. Cleanup and change isolation

  **What to do**:
  - Confirm only intended implementation/test files are touched.
  - Ensure unrelated existing user changes remain untouched.

  **References**:
  - `doeff/kleisli.py`, `doeff/program.py`, `doeff/rust_vm.py`, `doeff/handlers.py`, `doeff/presets.py`
  - `packages/doeff-vm/src/pyvm.rs`, `packages/doeff-vm/src/handler.rs`, `packages/doeff-vm/src/effect.rs`, `packages/doeff-vm/src/lib.rs`
  - `tests/public_api/test_types_001_kpc.py`, `tests/public_api/test_types_001_hierarchy.py`, `tests/public_api/test_types_001_handler_protocol.py`, `tests/public_api/test_kpc_macro_runtime_contract.py`, `tests/public_api/test_doeff13_hang_regression.py`

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`git-master`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 10)
  - **Parallel Group**: Wave 4
  - **Blocks**: 12
  - **Blocked By**: 9

  **Acceptance Criteria**:
  ```bash
  git status --short
  git diff --name-only
  ```
  - [x] changed-file list is limited to planned scope
  - [x] collateral changes outside planned scope are explicitly documented or absent
  - [x] summary written to `.sisyphus/evidence/kpc-change-scope.txt`

- [x] 12. Final readiness gate

  **What to do**:
  - Validate all must-have criteria and produce final execution summary.
  - Prepare commit plan by logical grouping (implementation/tests).

  **References**:
  - `.sisyphus/evidence/kpc-hang-baseline.md`
  - `.sisyphus/evidence/kpc-targeted-verification.txt`
  - `.sisyphus/evidence/kpc-negative-path-verification.txt`
  - `.sisyphus/evidence/kpc-change-scope.txt`

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`git-master`, `python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential final
  - **Blocks**: none
  - **Blocked By**: 10,11

  **Acceptance Criteria**:
  ```bash
  uv run pytest tests/public_api/test_kpc_macro_runtime_contract.py -q
  uv run pytest tests/public_api/test_doeff13_hang_regression.py -q
  uv run pytest -q
  ```
  - [x] all required tests green
  - [x] hang issue resolved with deterministic evidence and evidence files present
  - [x] hard-break negative checks pass (no reachable default `kpc` path)
  - [x] final execution summary written to `.sisyphus/evidence/kpc-final-readiness.md`

---

## Commit Strategy

| After Task Group | Message Template | Files |
|------------------|------------------|-------|
| Python migration (Task 4) | `refactor(runtime): align Python KPC path to macro model` | `doeff/*.py` targets |
| VM migration (Task 5) | `refactor(vm): remove KPC effect-handler runtime wiring` | `packages/doeff-vm/src/*.rs` targets |
| Test migration + hang fix (Tasks 6-8) | `test(spec): migrate KPC expectations and add doeff-13 hang regression` | `tests/**/*.py` |
| Verification-only metadata (optional) | `chore(test): tighten KPC/hang verification commands` | minimal |

---

## Success Criteria

### Verification Commands

```bash
uv run pytest tests/public_api/test_types_001_kpc.py -q
uv run pytest tests/public_api/test_types_001_hierarchy.py -q
uv run pytest tests/public_api/test_types_001_handler_protocol.py -q
uv run pytest tests/core/test_sa001_spec_gaps.py tests/core/test_do_methods.py tests/core/test_doexpr_hierarchy.py -q
uv run pytest -q
```

### Final Checklist

- [x] Macro call semantics implemented (`__call__` emits `Call` path)
- [x] `kpc` removed from defaults/presets/exports per compatibility decision
- [x] KPC handler runtime dependency removed or explicitly shimmed per decision
- [x] doeff-13 hang path covered by deterministic non-hanging regression test
- [x] targeted and full test suites pass

### Required Test Cases for New Files

`tests/public_api/test_kpc_macro_runtime_contract.py` MUST include at least:
- [x] `default_handlers_excludes_kpc`
- [x] `sync_preset_excludes_kpc`
- [x] `async_preset_excludes_kpc`
- [x] `legacy_kpc_effect_assumption_fails_fast`

`tests/public_api/test_doeff13_hang_regression.py` MUST include at least:
- [x] `do_handler_path_completes_within_3s`
- [x] `nested_do_handler_path_completes_within_3s`
- [x] `no_infinite_reentry_on_custom_handler`

---

## Decision Resolution

- Compatibility policy: **hard break now**.
- Old KPC-as-effect/handler behavior is removed in this implementation scope.
- Acceptance criteria enforce absence of legacy KPC handler dependency.
