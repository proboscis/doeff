---
id: TASK-CORE-008
title: Sync `doeff/*.pyi` stubs with runtime modules
module: core
status: done
priority: medium
due-date:
related-project:
related-spec:
related-feature:
code_path: doeff/
created: 2025-12-17
updated: 2025-12-17
tags: [task, core, typing, stubs, pyi]
---

# TASK-CORE-008 — Sync `doeff/*.pyi` stubs with runtime modules

## Description

Bring the type stubs under `doeff/` back in sync with runtime exports and signatures, with special
focus on `doeff/types.pyi` (`Effect`, `EffectBase`, vendored exports, and `EffectGenerator`).

## Acceptance Criteria

- [x] `doeff/types.pyi` exports match `doeff/_types_internal.py` (notably: `Effect`, `EffectFailure`,
      `EffectGenerator`, `Ok/Err/Result/Maybe`, graph types).
- [x] `doeff/program.pyi` matches `doeff/program.py` surface: `ProgramBase` core methods and correct
      return types for helpers like `sequence` / `traverse`.
- [x] `doeff/interpreter.pyi`, `doeff/cli/discovery.pyi`, `doeff/effects/pure.pyi` no longer contain
      undefined annotation symbols and match runtime signatures.
- [x] Validation: `uv run ruff check`, `uv run pytest`, and `uv run pyright` are executed.

## Implementation Notes

- Prefer stubs to mirror runtime signatures; update runtime annotations when stubs correct an
  obvious annotation bug (to prevent future drift).
- Use `__all__` lists in stubs to communicate intended exports and avoid unused-import warnings.

## Subtasks

- [x] Rewrite `doeff/types.pyi` to match `_types_internal` exports (vendored + core types)
- [x] Rewrite `doeff/program.pyi` to match `program.py` (and correct any drift)
- [x] Rewrite `doeff/interpreter.pyi`, `doeff/cli/discovery.pyi`, `doeff/effects/pure.pyi`
- [x] Run `uv run ruff check`, `uv run ruff format`, `uv run pytest`, `uv run pyright`

## Related

- Issue: [[ISSUE-CORE-403]]
- PR:

## Progress Log

### 2025-12-17
- Task created
- Synced `doeff/*.pyi` stubs and aligned `doeff/program.py` annotations
- Ran: `uv run ruff check`, `uv run ruff format`, `uv run pytest`, `uv run pyright`
