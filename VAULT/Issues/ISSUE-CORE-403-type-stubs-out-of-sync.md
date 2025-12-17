---
id: ISSUE-CORE-403
title: Type stubs (`.pyi`) are out of sync with runtime exports
module: core
status: closed
severity: medium
related-project:
related-spec:
related-task: TASK-CORE-008
related-feature:
created: 2025-12-17
updated: 2025-12-17
tags: [issue, core, typing, stubs, pyi]
---

# ISSUE-CORE-403 — Type stubs (`.pyi`) are out of sync with runtime exports

## Summary

Several `doeff/*.pyi` stubs have drifted from their runtime modules (missing exports, undefined
annotation symbols, and incorrect signatures). This breaks static typing (e.g., `pyright`) and IDE
auto-complete, and can mislead consumers about the public API.

## Examples

- `doeff/types.pyi` did not reflect key runtime exports from `doeff._types_internal`:
  - missing vendored re-exports (`Ok`, `Err`, `Result`, `Maybe`, graph types, etc.)
  - missing `EffectGenerator`
  - incorrect `Effect` / `EffectBase` shapes
- `doeff/program.pyi` diverged from `doeff/program.py`:
  - wrong return types for `first_success` / `sequence` / `traverse` (e.g., `KleisliProgramCall`)
  - missing core `ProgramBase` surface (`map`, `flat_map`, projections, etc.)
- `doeff/interpreter.pyi`, `doeff/cli/discovery.pyi`, `doeff/effects/pure.pyi` referenced undefined
  symbols (missing imports) and did not match runtime signatures.

## Desired Outcome

- `doeff` stubs match runtime module exports and signatures closely enough for:
  - `uv run pyright` to stop reporting missing/unknown import symbols caused by stub drift.
  - IDE users to see accurate function/class surfaces (especially `Effect` / `EffectBase`).
- Keep `doeff/types.pyi` aligned with `doeff/_types_internal.py` exports going forward.

## Resolution

- Fixed via [[TASK-CORE-008]].

### Fix Applied

- Synced stubs for `doeff/types.pyi`, `doeff/program.pyi`, `doeff/interpreter.pyi`,
  `doeff/cli/discovery.pyi`, `doeff/effects/pure.pyi`.
- Aligned a few `doeff/program.py` annotations with the corrected stub surface (`traverse`, varargs
  helpers).

### Verification

- `uv run pytest` (passes)
- `uv run ruff check` (repo currently has existing failures; stubs updated here are clean)
- `uv run pyright` (repo currently has existing failures; missing-symbol issues from stub drift are
  resolved)
