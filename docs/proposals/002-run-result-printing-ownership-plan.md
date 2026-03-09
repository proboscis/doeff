---
title: RunResult Printing Ownership Plan
status: in_progress
issue: ISSUE-CORE-525
---

# RunResult Printing Ownership Plan

## Goal

Make `run()` and `async_run()` pure execution APIs by default: they should return a `RunResult`
without printing doeff traces automatically. The CLI becomes the presentation layer responsible for
printing human-readable traces in text mode and embedding them in JSON mode.

No backward-compatibility helpers or opt-in shims for the old default behavior are planned.

## Status

- [x] Commit current traceback formatting / cache cleanup baseline
- [x] Document follow-up plan and status tracking
- [x] Change `run()` default behavior to not print doeff traces
- [x] Change `async_run()` default behavior to not print doeff traces
- [x] Update CLI flow so text mode prints exactly once and JSON mode stays stderr-clean
- [x] Update direct runtime tests to assert no default stderr output
- [x] Update CLI tests to assert CLI-owned rendering behavior
- [x] Re-review smells and validate targeted suites
- [ ] Commit follow-up implementation

## Constraints

- `run()` / `async_run()` remain the supported public execution APIs
- CLI text mode should print user-facing failure output
- CLI JSON mode should keep stderr clean for doeff trace output
- Do not preserve the old default-printing behavior via compatibility helpers

## Implementation Outline

1. Flip the runtime defaults in `doeff/rust_vm.py` so `run()` / `async_run()` do not print by default.
2. Keep explicit internal call sites honest (`sync_run`, CLI helpers, discovery paths) so they do not
   rely on runtime-owned printing.
3. In CLI text mode, render the attached doeff traceback exactly once when present; otherwise fall back
   to captured Python traceback or `Error: ...`.
4. In CLI JSON mode, include the doeff traceback in payload and avoid stderr trace noise.
5. Update tests to lock the contract from both layers:
   - runtime APIs: no default stderr trace output
   - CLI text: one printed doeff trace
   - CLI JSON: structured payload, clean stderr

## Notes

- The prior ISSUE-CORE-525 fixes already cleaned up pending-handler rendering, cache note propagation,
  and the stale traceback printing hacks. This plan is only for ownership of printing behavior.
