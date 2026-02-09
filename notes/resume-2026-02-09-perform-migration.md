# Resume Note - Perform Boundary Migration (2026-02-09)

## Scope
- Continue migration for ISSUE-CORE-483/484/485 with explicit `Perform(effect)` boundary semantics.
- Stabilize handler protocol and KPC paths under strict DoExpr runtime boundaries.

## What Was Completed
- Implemented Perform-lift hardening in `packages/doeff-vm/src/pyvm.rs`.
- Added effect-like normalization helper and applied it at key boundaries:
  - `lift_effect_to_perform_expr(...)`
  - `classify_yielded(...)`
  - `PyPerform::new(...)`
  - `PyDelegate::new(...)`
- Added boundary normalization where `WithHandler` wrappers are created directly:
  - `NestingGenerator::__next__`
  - module `run(...)`
  - module `async_run(...)`
- Updated `PyCreateContinuation::new(...)` to normalize `program` via Perform-lift.
- Removed callable fallback from `to_generator_strict(...)` to keep strict DoExpr boundary behavior.

## Validation Evidence
- Rebuilt local Rust extension:
  - `uv run maturin develop --manifest-path packages/doeff-vm/Cargo.toml`
- Targeted tests:
  - `uv run pytest tests/public_api/test_types_001_handler_protocol.py -q` -> `11 passed`
  - `uv run pytest tests/public_api/test_types_001_kpc.py -q` -> `20 passed`
- Public API suite:
  - `uv run pytest tests/public_api -q` -> `64 passed`

## Semgrep Status
- Ran:
  - `uv run semgrep --config .semgrep.yaml packages/doeff-vm/src doeff/rust_vm.py tests/public_api`
- Result: 12 findings remain.
- Main buckets:
  - `doeff/rust_vm.py`: `doeff-no-typing-any-in-public-api`
  - `packages/doeff-vm/src/pyvm.rs`: `spec-gap-SA-001-G08-classify-duck-typing` (rule is broad and flags `getattr` usages globally in the file)

## Current Working Tree (non-clean)
Modified files currently include:
- `.semgrep.yaml`
- `doeff/__init__.py`
- `doeff/_types_internal.py`
- `doeff/program.py`
- `doeff/rust_vm.py`
- `packages/doeff-vm/doeff_vm/__init__.py`
- `packages/doeff-vm/src/do_ctrl.rs`
- `packages/doeff-vm/src/effect.rs`
- `packages/doeff-vm/src/pyvm.rs`
- `packages/doeff-vm/src/vm.rs`
- `packages/doeff-vm/src/yielded.rs`
- `tests/public_api/test_types_001_doctrl_exports.py`
- `tests/public_api/test_types_001_hierarchy.py`
- `tests/public_api/test_types_001_validation.py`

## Resume Steps (next session)
1. Rebuild extension immediately after pulling up session:
   - `uv run maturin develop --manifest-path packages/doeff-vm/Cargo.toml`
2. Reconfirm current green baseline:
   - `uv run pytest tests/public_api -q`
3. Start ISSUE-CORE-486 (handler return normalization):
   - Normalize handler returns to explicit DoExpr control forms (`DoExpr` / `Perform` / `Pure`) at VM boundary points.
   - Add/update focused public API tests first, then implement minimal runtime changes.
4. Then start ISSUE-CORE-487 (composition migration away from effect-level map):
   - Ensure composition semantics rely on DoExpr control operators, not effect-level shortcuts.
5. Re-run verification set:
   - `uv run pytest tests/public_api -q`
   - `uv run semgrep --config .semgrep.yaml packages/doeff-vm/src doeff/rust_vm.py tests/public_api`

## Notes
- Do not assume clean branch.
- Do not revert unrelated user edits.
- No commit has been created in this session.
