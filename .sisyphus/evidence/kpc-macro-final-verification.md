# KPC Macro Final Verification

Date: 2026-02-16 (Asia/Tokyo)

## Commands Run

1. `uv run pytest -q`
   - Result: PASS
   - Summary: `434 passed, 6 skipped, 1 warning in 27.27s`

2. `cargo build && cargo test` (workdir: `packages/doeff-vm`)
   - Result: PASS
   - Summary: crate builds; unit tests `138 passed, 0 failed`; doc tests `0 passed, 0 failed`

3. KPC artifact grep checks
   - `grep pattern: KleisliProgramCall|_effective_runtime_handlers|PyKPC` in `doeff/**/*.py`
   - `grep pattern: PyKPC|KpcHandler|ConcurrentKpc|KleisliProgramCall|_effective_runtime_handlers` in `packages/doeff-vm/src/**/*.rs`
   - `grep pattern: KleisliProgramCall|_effective_runtime_handlers|PyKPC|kpc|concurrent_kpc` in `packages/doeff-vm/doeff_vm/**/*.py`
   - `grep pattern: KleisliProgramCall|_effective_runtime_handlers|PyKPC|kpc|concurrent_kpc` in `packages/doeff-pinjected/**/*.py`
   - Result: PASS (no matches in all targeted runtime/package paths)

4. Bounded hang regression check
   - Command: `uv run python -c "import subprocess,sys; r=subprocess.run(['pytest','tests/public_api/test_doeff13_hang_regression.py','-q'], timeout=3.0); sys.exit(r.returncode)"`
   - Result: PASS
   - Summary: `4 passed in 0.02s`

5. Macro expansion type check (strict)
   - Command: `uv run python -c "from doeff import do; from doeff_vm import Call, Pure; f = do(lambda: 42); result = f(); assert isinstance(result, Call), type(result); assert isinstance(result.f, Pure), type(result.f)"`
   - Result: PASS

6. Call constructor strictness check
   - Command: `uv run python -c "from doeff_vm import Call, Pure\ntry:\n    Call(lambda: None, [], {})\n    raise SystemExit(1)\nexcept TypeError:\n    pass\n"`
   - Result: PASS

## Additional Regression Checks

- `uv run pytest tests/effects/test_effect_combinations.py::TestListenCaptureLaw -q` -> `8 passed`
- `uv run pytest tests/core/test_call_doexpr_evaluation.py -q` -> `6 passed`
- `uv run pytest tests/public_api/test_kpc_macro_expansion.py -q` -> `7 passed`
- `uv run pytest tests/public_api/test_types_001_doctrl_exports.py -q` -> `5 passed`
- `uv run pytest tests/public_api/test_types_001_handler_protocol.py::TestHP11DoDecoratedHandler -q` -> `4 passed`
- `uv run pytest tests/public_api/test_types_001_handler_protocol.py::TestHP11DoDecoratedHandler packages/doeff-vm/tests/test_package_exports.py -q` -> `8 passed`
- `uv run pytest tests/misc/test_reader_hashable_keys.py -q` -> `2 passed`

## Strict Spec Enforcement (TDD)

- RED: tightened strict-spec tests failed as expected:
  - `tests/public_api/test_kpc_macro_expansion.py::test_kernel_is_exposed_from_call_f_as_pure`
  - `tests/public_api/test_types_001_doctrl_exports.py` strict `Call` constructor tests
- GREEN: implemented strict behavior and reran target tests:
  - `uv run pytest tests/public_api/test_kpc_macro_expansion.py tests/public_api/test_types_001_doctrl_exports.py -q`
  - Result: `12 passed`

## Notes

- Rust build emits non-fatal warnings (dead code and non-upper-case associated constants); no failures.
- Implementation behavior observed:
  - `KleisliProgram.__call__()` returns `Call` and classifies args to DoExpr nodes.
  - `KleisliProgram.__call__()` now emits strict shape `Call(Pure(kernel), args=[DoExpr], kwargs={...DoExpr...})`.
  - `Call` constructor now enforces DoExpr-only inputs for `f`, positional args, and kwarg values.
  - `@do` handler validation is enforced on the Python side (module wrappers), and invalid handlers raise at handler installation time:
    - `WithHandler(handler=..., expr=...)`
    - `run(..., handlers=[...])` / `async_run(..., handlers=[...])`
  - The extension submodule path is also patched at import-time so `import doeff_vm.doeff_vm` uses the same validated `WithHandler`/`run`/`async_run` entrypoints.
  - VM no longer applies handler-specific auto-unwrap fallback in `KleisliProgram.__call__`; invalid `@do` handler signatures are treated as definition errors.
