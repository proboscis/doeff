---
title: RunResult Printing Ownership Plan
status: superseded
issue: ISSUE-CORE-525
---

# RunResult Printing Ownership Plan

> **Status note (2026-07)**: This plan is largely **superseded**. `RunResult` no longer exists --
> `run(doexpr)` returns the raw value directly or raises an exception on error.
> The printing-ownership concern was resolved as part of the broader API simplification:
> `run()` enriches exceptions with `__doeff_traceback__` and prints the doeff traceback to stderr,
> but does not wrap the result in a `RunResult` container. The final "Commit follow-up
> implementation" step was never completed as a separate commit because the changes landed
> as part of the `run()` API redesign.

## Goal

Make `run()` a pure execution API by default: it should return the result value
without printing doeff traces automatically. The CLI becomes the presentation layer responsible for
printing human-readable traces in text mode and embedding them in JSON mode.

No backward-compatibility helpers or opt-in shims for the old default behavior are planned.

## Status

- [x] Commit current traceback formatting / cache cleanup baseline
- [x] Document follow-up plan and status tracking
- [x] Change `run()` default behavior to not print doeff traces
- [x] Update CLI flow so text mode prints exactly once and JSON mode stays stderr-clean
- [x] Update direct runtime tests to assert no default stderr output
- [x] Update CLI tests to assert CLI-owned rendering behavior
- [x] Re-review smells and validate targeted suites
- [ ] ~~Commit follow-up implementation~~ (superseded -- changes landed as part of `run()` API redesign)

## Constraints

- `run()` is the supported public execution API (`async_run()` was removed -- use `run(scheduled(...))`)
- CLI text mode should print user-facing failure output
- CLI JSON mode should keep stderr clean for doeff trace output
- Do not preserve the old default-printing behavior via compatibility helpers

## Implementation Outline

1. `run(doexpr)` takes a single argument, returns the raw value, and raises on error.
   On error, it enriches the exception with `__doeff_traceback__` and prints the doeff
   traceback to stderr.
2. CLI text mode renders the doeff traceback exactly once when present; otherwise falls back
   to captured Python traceback or `Error: ...`.
3. CLI JSON mode includes the doeff traceback in payload and avoids stderr trace noise.
4. Tests lock the contract from both layers:
   - runtime API: no default stderr trace output (except on error)
   - CLI text: one printed doeff trace
   - CLI JSON: structured payload, clean stderr

## Notes

- The prior ISSUE-CORE-525 fixes already cleaned up pending-handler rendering, cache note propagation,
  and the stale traceback printing hacks.
- `RunResult` no longer exists as a concept. `run()` returns the raw value directly.
- `async_run()` was removed. Use `run(scheduled(...))` for async programs.
