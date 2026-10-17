# SA-005 Progress

## Session

- Session ID: SA-005
- Date: 2026-02-08
- Specs:
  - `specs/vm/SPEC-008-rust-vm.md`
  - `specs/vm/SPEC-009-rust-vm-migration.md`
  - `specs/core/SPEC-TYPES-001-program-effect-separation.md`
- Implementation directories:
  - `doeff`
  - `packages/doeff-vm`

## Stage Tasks

| Task | Active form | Status |
|---|---|---|
| SA-005 Phase 1: Parallel section review | Reviewing spec sections | completed |
| SA-005 Phase 2: Gap classification + report | Classifying gaps | completed |
| SA-005 Phase 2.5: Auto-resolution review gate | Reviewing auto-resolutions | requires_user_decision |

## Phase 1 Review Units

| Unit | Scope | Spec section coverage | Target implementation files |
|---|---|---|---|
| U1 | SPEC-008 foundations + data structures | `SPEC-008` Summary/Design/Core Principles/Rust Data Structures (`~1-1800`) | `packages/doeff-vm/src/{py_shared.rs,frame.rs,segment.rs,continuation.rs,value.rs,effect.rs,do_ctrl.rs,handler.rs,dispatch.rs,scheduler.rs}` |
| U2 | SPEC-008 execution/state machine | `SPEC-008` Scheduler API, pending Python calls, step transitions, invariants (`~1800-end`) | `packages/doeff-vm/src/{python_call.rs,step.rs,yielded.rs,driver.rs,vm.rs,pyvm.rs,scheduler.rs,dispatch.rs,error.rs}` + `doeff/rust_vm.py` |
| U3 | SPEC-009 API (0-5) | Design, entrypoints, RunResult, Program/@do, effects, handlers | `doeff/{__init__.py,run.py,do.py,program.py,handlers.py,presets.py,rust_vm.py,_types_internal.py}` + `packages/doeff-vm/src/{lib.rs,pyvm.rs,handler.rs}` |
| U4 | SPEC-009 API (6-11) | WithHandler, standard handlers, imports, non-exposed API, migration, invariants | `doeff/{handlers.py,presets.py,__init__.py,effects/__init__.py}` + `packages/doeff-vm/src/{lib.rs,handler.rs,dispatch.rs,scheduler.rs}` |
| U5 | SPEC-TYPES-001 hierarchy + @do | Context -> @do features (`~1-532`) | `doeff/{program.py,do.py,effects/base.py,effects/_program_types.py,kleisli.py,_types_internal.py}` + `packages/doeff-vm/src/{effect.rs,do_ctrl.rs,yielded.rs,handler.rs}` |
| U6 | SPEC-TYPES-001 classify/migration/callstack | Call stack, classify impact, migration, open questions (`~532-end`) | `packages/doeff-vm/src/{yielded.rs,pyvm.rs,step.rs,frame.rs,continuation.rs}` + `doeff/effects/callstack.py` + `specs/audits/SA-001..SA-004/*` |

## Unit Collection Status

| Unit | Background task | Status | Notes |
|---|---|---|---|
| U1 | `bg_ebe85a99` | completed | 14 findings: 5 MATCH, 5 DIVERGENCE, 2 MISSING, 1 DISCUSSION, 1 CONTRADICTION |
| U2 | `bg_e2ac4ce5` | completed | 8 findings: 5 MATCH, 2 DIVERGENCE, 0 MISSING, 1 DISCUSSION |
| U3 | `bg_3df69d7d` | completed | 8 findings: 6 MATCH, 2 DIVERGENCE |
| U4 | `bg_988eb758` | completed | 8 findings: 6 MATCH, 1 MISSING, 1 DISCUSSION |
| U5 | `bg_cda4a2ab` | completed | 10 findings: 6 MATCH, 2 DIVERGENCE, 1 MISSING, 1 DISCUSSION |
| U6 | `bg_7791d551` | completed | 8 findings: 4 MATCH, 2 DIVERGENCE, 1 MISSING, 1 DISCUSSION |

## Context Gathering Evidence

- Spec/impl scope discovery: `bg_612e1ad1`
- Existing audit artifact conventions: `bg_bf7a7d80`
- External naming/context references: `bg_436002df`
- Semgrep TDD workflow references: `bg_1faa967f`

## Phase 1 Aggregation (Interim)

- Completed units: U1-U5 (U6 pending)
- Pre-dedup candidate divergence themes to verify after U6:
  - Effect/scheduler pyclass structure drift between SPEC-008 R11 text and current marker-based implementation paths
  - Handler signature/API drift (`DispatchEffect` paths vs spec opaque-bound effect contract)
  - SPEC-009 API surface drift around normalization/input contract in `doeff/rust_vm.py`
  - SPEC-009 `kpc` export/default handler composition checks
  - SPEC-TYPES-001 hierarchy terminology mismatch (`Program=DoExpr`, `DerivedProgram/PureProgram`) vs current runtime naming
  - Potential spec-internal contradiction in SPEC-008 about effect enum removal vs legacy snippets

## Cross-Reference Filter (Interim)

- Already-known/open in SA-004 (candidate carry-over, avoid double counting as new):
  - `SPEC-008` scheduler pyclass contracts vs marker parsing (`SA-004-G02`)
  - `SPEC-008` handler signature drift (`SA-004-G03`)
  - `SPEC-009` preset composition drift (`SA-004-G04`)
  - `SPEC-TYPES` `DerivedProgram` naming drift (`SA-004-G07`)
- Previously resolved in SA-002/SA-003 (verify regression before classifying as new):
  - `default_handlers`/presets behavior and contract (`SA-002-G05`)
  - strict DoExpr normalization expectations (`SA-003-Q02`, `SA-002-G09` lineage)
  - KPC transitional strategy handling (`SA-001-G10`, `SA-004-G08` lineage)

## Phase 1 Completion Notes

- All review units completed: U1-U6
- Candidate findings collected for Phase 2 classification: 56 unit-level entries before dedup
- Duplicate/carry-over filtering applied using prior sessions SA-001..SA-004 before assigning SA-005 IDs

## Phase 2 Status

- `report.md` written: `specs/audits/SA-005/report.md`
- `resolutions.md` written: `specs/audits/SA-005/resolutions.md`
- C/Q items present: waiting for user resolutions before Phase 2.5/3

## Phase 2.5 Gate Result

- Reviewer outcome: `REQUIRES_USER_DECISION`
- Reviewer session: `ses_3c32b9482ffedtvvRLwNKLe59e`
- Blocking unresolved rows in `resolutions.md`:
  - `SA-005-C01`
  - `SA-005-G03`
  - `SA-005-G07`
  - `SA-005-Q01`
  - `SA-005-Q02`
  - `SA-005-Q03`

## Item Tracker

| ID | Type | Severity | Enforcement | Status | Test/Rule | Review |
|---|---|---|---|---|---|---|
| SA-005-C01 | Contradiction | n/a | - | pending-resolution | - | - |
| SA-005-G01 | Gap | Critical | test + semgrep | pending | - | - |
| SA-005-G02 | Gap | Critical | test + semgrep | pending | - | - |
| SA-005-G03 | Gap | Moderate | test | pending-resolution | - | - |
| SA-005-G04 | Gap | Critical | test | pending | - | - |
| SA-005-G05 | Gap | Moderate | test | pending | - | - |
| SA-005-G06 | Gap | Moderate | test | pending | - | - |
| SA-005-G07 | Gap | Critical | test + semgrep | pending-resolution | - | - |
| SA-005-G08 | Gap | Minor | semgrep | pending | - | - |
| SA-005-Q01 | Discussion | n/a | - | pending-resolution | - | - |
| SA-005-Q02 | Discussion | n/a | - | pending-resolution | - | - |
| SA-005-Q03 | Discussion | n/a | - | pending-resolution | - | - |

## Dependency Ledger

- fact -> issue -> auto-resolve/discussion -> action -> dependencies
- `SPEC-008` effect-enum conflict -> SA-005-C01 -> discussion-required -> discuss -> []
- effect pyclass payload mismatch -> SA-005-G01 -> auto-fix-code -> fix-code -> [SA-005-C01]
- scheduler typed effects missing in `effect.rs` -> SA-005-G02 -> auto-fix-code -> fix-code -> [SA-005-C01]
- handler contract drift (`DispatchEffect`) -> SA-005-G03 -> discussion-required -> discuss -> [SA-005-C01]
- Python-side normalization in wrapper -> SA-005-G04 -> auto-fix-code -> fix-code -> []
- run input/error contract mismatch -> SA-005-G05 -> auto-fix-code -> fix-code -> [SA-005-G04]
- `kpc` export omission -> SA-005-G06 -> auto-fix-code -> fix-code -> [SA-005-Q01]
- Program/DoExpr hierarchy drift -> SA-005-G07 -> discussion-required -> discuss -> [SA-005-C01]
- classifier flow-order drift -> SA-005-G08 -> auto-fix-code -> fix-code -> [SA-005-G07]
- preset composition ambiguity -> SA-005-Q01 -> discussion-required -> discuss -> []
- continuation representation split -> SA-005-Q02 -> discussion-required -> discuss -> [SA-005-C01]
- callstack bridge ambiguity -> SA-005-Q03 -> discussion-required -> discuss -> [SA-005-G07]

## Phase 1 Evidence Ledger

- U1 evidence source: `bg_ebe85a99`
  - Example: `SPEC-008:823-859` vs `packages/doeff-vm/src/effect.rs:16-29` (DIVERGENCE)
  - Example: `SPEC-008:864-907` vs `packages/doeff-vm/src/effect.rs` (MISSING scheduler pyclass set)
  - Example: `SPEC-008:1127-1141` vs `packages/doeff-vm/src/handler.rs:42-51` (DIVERGENCE)
- U2 evidence source: `bg_e2ac4ce5`
  - Example: `SPEC-008:2417-2432` vs `packages/doeff-vm/src/pyvm.rs:550-670` (DIVERGENCE)
  - Example: `SPEC-008:4376` vs `packages/doeff-vm/src/vm.rs:1098` (DIVERGENCE)
- U3 evidence source: `bg_3df69d7d`
  - Example: `SPEC-009:238-243` vs `doeff/rust_vm.py:13-31` (DIVERGENCE)
  - Example: `SPEC-009:203-219` vs `doeff/rust_vm.py:27-31`, `packages/doeff-vm/src/pyvm.rs:508-519` (DIVERGENCE)
- U4 evidence source: `bg_988eb758`
  - Example: `SPEC-009:746-760` vs `doeff/handlers.py:7-22` (MISSING `kpc` export)
  - Example: `SPEC-009:769-771` vs `doeff/presets.py:15-27` (DISCUSSION)
- U5 evidence source: `bg_cda4a2ab`
  - Example: `SPEC-TYPES-001:111-116` vs `doeff/program.py:248-267` (DIVERGENCE)
  - Example: `SPEC-TYPES-001:145-147` vs `doeff/program.py:332-460` (MISSING named DoThunk variants)
- U6 evidence source: `bg_7791d551`
  - Example: `SPEC-TYPES-001:744-752` vs `packages/doeff-vm/src/pyvm.rs:548-676` (DIVERGENCE)
  - Example: `SPEC-TYPES-001:637-645` vs `doeff/effects/callstack.py:34-41`, `packages/doeff-vm/src/pyvm.rs:653-655` (MISSING)

## False-Positive and Duplicate Filter Log

- False positives removed:
  - U1 initial concern on `Value::Handlers` conversion removed after verification (`packages/doeff-vm/src/value.rs:73`) as spec-aligned behavior.
- Duplicate/carry-over handling:
  - Grouped and marked for carry-over verification instead of direct SA-005 ID assignment where matching SA-004 open items (`G02/G03/G04/G07/G08` lineage).
  - Grouped and marked regression-check-required for items previously resolved in SA-002/SA-003 (`G05`, DoExpr normalization lineage).
