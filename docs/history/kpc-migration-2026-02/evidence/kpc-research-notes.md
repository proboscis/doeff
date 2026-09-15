# KPC Migration Research Notes

Timestamp: 2026-02-15T05:57:00Z
Session: ses_3a07f8917ffe0u1EjSjNBtR22z

## Highest-Signal Internal Anchors

- Python defaults/export surface
  - `doeff/rust_vm.py:133` - `default_handlers()` includes `kpc` sentinel in required list.
  - `doeff/handlers.py:3` - module docs/export contract still advertises `kpc`.
  - `doeff/presets.py:26` - async preset is built from `default_handlers()`, inheriting `kpc`.

- Rust VM KPC runtime wiring
  - `packages/doeff-vm/src/pyvm.rs:2888` - `kpc` sentinel registration (`KpcHandlerFactory`).
  - `packages/doeff-vm/src/handler.rs:644` - `KpcHandlerFactory` active.
  - `packages/doeff-vm/src/handler.rs:1608` - concurrent KPC handler path active.
  - `packages/doeff-vm/src/effect.rs:50` - `PyKPC` still modeled as `PyEffectBase` subclass.

- Hang/reentry clues
  - `tests/public_api/test_types_001_handler_protocol.py:389` - existing doeff-13 skip marker.
  - `packages/doeff-vm/src/handler.rs:626` - KPC arg extraction/unwrap branch.
  - `packages/doeff-vm/src/vm.rs:2084` - `GetHandlers` path implicated in re-entry cycle.

## External Timeout/Hang Mitigation Notes

- `pytest` + `faulthandler_timeout` is the official stack-dump path for stuck tests.
- `pytest-timeout` (`--timeout`, `--timeout-method=thread`) gives deterministic per-test cutoffs.
- Keep outer process deadline wrapper in CI to avoid indefinite hangs if in-test watchdog fails.

## Immediate Application to Current Plan

- Task 4 should cut Python `kpc` default/preset/export contract first.
- Task 5 should remove Rust sentinel registration and KPC handler dispatch wiring.
- Task 7 should verify doeff-13 path with bounded regression tests (`3.0s` per case).
