# Tracking Issue: Correctness-First Follow-ups (Post SA-008)

Date: 2026-02-08
Session: SA-008
Scope: Re-review by correctness (not just “works on some paths”)

## Architecture Decision (Non-negotiable)

Correctness baseline for next fixes:

1. **Single source of truth for runtime classes is Rust**.
2. `DoExpr` / `DoCtrlBase` / `EffectBase` semantics must be implemented in Rust only and exposed to Python.
3. Python must not carry parallel concrete hierarchy implementations for runtime base classes.
4. Boundary checks must be strict type checks, not duck-typing/fallback probing.
5. Error signaling should be Python exceptions where possible, with clear messages.

## Re-reviewed Issues (Correctness-first)

| ID | Issue | Correctness Priority | Status | Evidence |
|---|---|---|---|---|
| C0 | **Disconnected/dual hierarchy by design** (Rust runtime bases + Python concrete hierarchy + bridges) | P0 | CONFIRMED | Python-side concrete bases exist in `doeff/program.py` and `doeff/_types_internal.py`; Rust-side bases exist in `packages/doeff-vm/src/pyvm.rs` (`PyEffectBase`, `PyDoCtrlBase`); bridge hacks in `_types_internal.py` (`__subclasscheck__`, dynamic `__bases__`). |
| C1 | Boundary classification still contains non-strict heuristics (`to_generator`, `hasattr`, `getattr`, shape probing) | P0 | CONFIRMED | `packages/doeff-vm/src/pyvm.rs` (`to_generator_strict`), `packages/doeff-vm/src/handler.rs` and `packages/doeff-vm/src/scheduler.rs` use attribute probing and fallback field paths. |
| C2 | `run()` wrapper throws unhandled-effect exceptions instead of pure `RunResult` contract | P1 | CONFIRMED | `doeff/rust_vm.py` `_raise_unhandled_effect_if_present` raises `TypeError` when matching “UnhandledEffect”. |
| C3 | Store/state integration mismatch in end-to-end behavior | P1 | CONFIRMED | Rust entrypoint seeds store in `packages/doeff-vm/src/pyvm.rs` (run/async_run), but direct smoke checks still show stale `raw_store`/`None` value paths; integration path needs diagnosis. |
| C4 | `EffectBase.map/flat_map` remains generator-wrapper model (not strict DoCtrl-native path) | P1 | CONFIRMED | `doeff/_types_internal.py` `EffectBase.map`/`flat_map` return `GeneratorProgram`. |
| C5 | `@do` decorator input boundary is not strict (`do(42)` accepted) | P2 | CONFIRMED | `doeff/do.py` lacks callable boundary validation for decorator argument; direct runtime check shows no error on non-callable. |
| C6 | Stale tests reference legacy DoThunk-era model | P2 | CONFIRMED | `tests/public_api/test_types_001_hierarchy.py`, `tests/public_api/test_types_001_validation.py`, `tests/core/test_sa001_spec_gaps.py`. |

## Why C0 is still real (even if some paths work)

Some runtime paths now accept KPC in `WithHandler`, but that does **not** resolve architectural correctness:

- Accepting one path is not equivalent to unified hierarchy semantics.
- Current model still relies on bridge layers between Python concrete types and Rust concrete types.
- This violates the single-source runtime type principle above.

Therefore C0 remains a **real correctness issue**, not just a historical claim.

## Correctness-First Priority Order

### P0 (must fix first)

1. **Unify hierarchy ownership to Rust-only runtime bases**.
2. **Remove boundary heuristics/fallbacks in classifier/dispatch paths**.

### P0 Hotspots (exact files)

- `packages/doeff-vm/src/pyvm.rs`
  - `to_generator_strict` currently accepts by `to_generator` attribute and `send/throw` shape checks.
  - `WithHandler.expr` validation still mixes strict base checks with `to_generator` attribute check.
- `packages/doeff-vm/src/handler.rs`
  - KPC/effect parsing still uses broad `getattr`/shape probing and candidate heuristics.
- `packages/doeff-vm/src/scheduler.rs`
  - scheduler effects parse via field probing + fallback field names (`futures/items`, `task/task_id`).

These three files define the real correctness boundary and should be fixed before lower-priority cleanup.

### P1 (runtime contract correctness)

3. Decide and enforce one `run()` failure contract (exception-first vs `RunResult.Err`) and make tests/specs consistent.
4. Fix store/state propagation integration bug end-to-end.
5. Decide and enforce `EffectBase.map/flat_map` model (strict DoCtrl-native if required by spec direction).

### P2 (safety and migration cleanup)

6. Add strict decorator input validation in `@do`.
7. Migrate stale DoThunk-era tests to current architecture decisions.

## Task Checklist

- [ ] T0: Draft explicit architecture note in specs: “Runtime type classes are Rust-owned; Python provides exposure/protocol only.”
- [ ] T1: Remove Python-side concrete runtime base duplication where applicable; keep only exposure/protocol layers.
- [ ] T2: Replace duck/fallback boundary checks with strict Rust-base checks.
- [ ] T3: Resolve `run()` error contract and align public tests.
- [ ] T4: Add focused regression tests for store seeding/final state propagation and Modify/Get behavior.
- [ ] T5: Decide/update `EffectBase.map/flat_map` model and corresponding tests.
- [ ] T6: Add `do()` decorator callable boundary validation.
- [ ] T7: Update stale DoThunk-reliant tests.

## Exit Criteria

- Runtime hierarchy ownership is unambiguous (Rust-only for concrete runtime bases).
- No fallback/duck boundary paths remain in critical classifier/dispatch code.
- Store/state end-to-end regressions are green.
- Public API contract tests match final decision on error signaling.
- SA-008 tests remain green while stale legacy tests are either migrated or intentionally retired.

## External Design Basis (for this policy)

- PyO3: class inheritance should be explicit (`#[pyclass(subclass)]` opt-in), not accidental.
- pydantic-core style: runtime classes are Rust-backed; Python side exposes API/types, not parallel concrete runtime implementations.
- polars style: keep protocol/typing boundary separate from Rust concrete runtime objects; avoid treating fallback paths as normative behavior.

Interpretation for doeff:
- Runtime concrete classes should be Rust-only.
- Python layer should be exposure/protocol/documentation boundary, not a second concrete hierarchy.
- Fallback/duck paths are migration aids at best, never correctness baseline.
