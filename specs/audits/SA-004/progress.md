# SA-004 Progress

## Session

- Session ID: SA-004
- Date: 2026-02-08
- Specs:
  - `specs/vm-architecture/SPEC-008-rust-vm.md`
  - `specs/vm-architecture/SPEC-009-rust-vm-migration.md`
  - `specs/SPEC-TYPES-001-program-effect-separation.md`
- Implementation directories:
  - `doeff`
  - `packages/doeff-vm`

## Stage 1 Tasks (Discovery)

| Task | Active form | Status |
|---|---|---|
| SA-004 Phase 1: Parallel section review | Reviewing spec sections | completed |
| SA-004 Phase 2: Gap classification + report | Classifying gaps | completed |

## Phase 1 Review Units

| Unit | Scope | Spec section coverage | Target implementation files |
|---|---|---|---|
| U1 | SPEC-008 foundations + data structures | `SPEC-008` Summary/Design/Core Principles/Rust Data Structures (`~1-1800`) | `packages/doeff-vm/src/{py_shared.rs,frame.rs,segment.rs,continuation.rs,value.rs,effect.rs,do_ctrl.rs,handler.rs,dispatch.rs,scheduler.rs}` |
| U2 | SPEC-008 execution/state machine | `SPEC-008` Scheduler API, pending Python calls, step transitions, invariants (`~1800-end`) | `packages/doeff-vm/src/{python_call.rs,step.rs,yielded.rs,driver.rs,vm.rs,pyvm.rs,scheduler.rs,dispatch.rs,error.rs}` + `doeff/rust_vm.py` |
| U3 | SPEC-009 API (0-5) | Design, entrypoints, RunResult, Program/@do, effects, handlers | `doeff/{__init__.py,run.py,do.py,program.py,handlers.py,presets.py,rust_vm.py,_types_internal.py}` + `packages/doeff-vm/src/{lib.rs,pyvm.rs,handler.rs}` |
| U4 | SPEC-009 API (6-11) | WithHandler, standard handlers, imports, non-exposed API, migration, invariants | `doeff/{handlers.py,presets.py,__init__.py,effects/__init__.py}` + `packages/doeff-vm/src/{lib.rs,handler.rs,dispatch.rs,scheduler.rs}` |
| U5 | SPEC-TYPES-001 hierarchy + @do | Context -> @do features (`~1-532`) | `doeff/{program.py,do.py,effects/base.py,effects/_program_types.py,kleisli.py,_types_internal.py}` + `packages/doeff-vm/src/{effect.rs,do_ctrl.rs,yielded.rs,handler.rs}` |
| U6 | SPEC-TYPES-001 classify/migration/callstack | Call stack, classify impact, migration, open questions (`~532-end`) | `packages/doeff-vm/src/{yielded.rs,pyvm.rs,step.rs,frame.rs,continuation.rs}` + `doeff/effects/callstack.py` + `specs/audits/SA-001..SA-003/*` |

## Unit Collection Status

| Unit | Background task | Status | Notes |
|---|---|---|---|
| U1 | `bg_29e3a166` | completed | SPEC-008 foundational/data-structure review delivered; requires dedup and false-positive filter |
| U2 | `bg_9ce924e6` | completed | SPEC-008 execution/state-machine/invariants reviewed; claims merged with direct verification |
| U3 | `bg_6b103c72` | completed | SPEC-009 sections 0-5 mostly conformant; minor drifts captured |
| U4 | `bg_a53fb5e9` | completed | SPEC-009 sections 6-11 review delivered; one moderate divergence candidate |
| U5 | `bg_ff7450d0` | completed | SPEC-TYPES-001 sections 1-4 reviewed; moderate+minor candidates reported |
| U6 | `bg_dd87da5f` | completed | SPEC-TYPES-001 sections 5-10 reviewed; mostly aligned with test-only and carry-over policy candidates |

## Context Gathering Evidence

- Spec section inventory (explore): `bg_06190fee`
- Implementation mapping (explore): `bg_aa16cf5b`
- Workflow references (librarian): `bg_6f92f837`
- Selected spec scope for SA-004:
  - `specs/vm-architecture/SPEC-008-rust-vm.md`
  - `specs/vm-architecture/SPEC-009-rust-vm-migration.md`
  - `specs/SPEC-TYPES-001-program-effect-separation.md`

## Baseline Regression Snapshot

- `uv run pytest tests/core/test_sa001_spec_gaps.py tests/core/test_sa002_spec_gaps.py tests/core/test_sa003_spec_gaps.py`
  - Result: `44 passed`
- `semgrep --config specs/audits/SA-003/semgrep/rules.yml doeff/ packages/`
  - Result: `0 findings`

## Interim Classification Notes (Before full merge)

- Candidate gaps from completed units (dedup pending):
  - Effect pyclass field contracts in `SPEC-008` vs `packages/doeff-vm/src/effect.rs`
  - Scheduler effect pyclass contracts in `SPEC-008` vs scheduler marker-based parsing
  - `RustHandlerProgram`/`RustProgramHandler` signature drift from `SPEC-008` text
  - `sync_preset` composition drift from `SPEC-009` preset example text
  - Minor naming drift in `Modify(key, fn)` wording vs implementation (`func`/`f`)
  - SPEC-TYPES-001 naming/structure drift candidates around `PureProgram`/`DerivedProgram` vs current implementation naming/types
  - Test-only legacy KPC strategy attr lookup candidate (`auto_unwrap_strategy`) in test cfg path
- Filtered false-positive from U1:
  - `Value::Handlers` identity conversion is currently spec-aligned in `packages/doeff-vm/src/value.rs:73`.

## Dedup / Carry-over Checks

- Overlap detected with previous audit IDs (needs final merge decision):
  - SA-004-G05 (interim) overlaps prior `SA-001-G15` (handler signature drift)
  - SA-004-G06 (interim) overlaps prior `SA-002-G05` (default/preset contract)
  - SA-004-Q02 (interim) overlaps prior `SA-001-G10` lineage (`auto_unwrap_strategy` migration)
- Decision rule for SA-004 final report:
  - If already resolved and still green in SA tests, classify as `closed/carry-over`, not new gap.
  - If behavior re-diverged from post-SA baseline, classify as new SA-004 gap with explicit regression evidence.

## Phase 2 Completion Notes

- All six review units merged (U1-U6).
- Direct verification performed on disputed claims before classification.
- Final classified outputs written to:
  - `specs/audits/SA-004/report.md`
  - `specs/audits/SA-004/resolutions.md`
- Gating status:
  - C items: none
  - Q items: present (requires user resolutions before Phase 3-7)

## Phase 4 Inputs (Semgrep Research)

- Semgrep template/reference batch received (`bg_1913c4f9`): 8 reusable patterns with official docs links and test approach.
- Planned adaptation for SA-004 (to be narrowed after final gap IDs):
  - Structural drift rules for forbidden legacy patterns
  - `ruleid`/`ok` style tests via `semgrep --test`
  - schema validation via `semgrep --validate --config <rule-file>`
