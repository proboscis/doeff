---
id: TASK-CORE-007
title: Support hashable keys for Reader env (ask/local)
module: core
status: todo
priority: medium
due-date:
related-project:
related-spec:
related-feature:
code_path: doeff/effects/reader.py
created: 2025-12-15
updated: 2025-12-15
tags: [task, core, reader, ask, local, typing, di]
---

# TASK-CORE-007 — Support hashable keys for Reader env (ask/local)

## Description

Update the Reader environment (`ask` / `local`) to accept any hashable key type (e.g., a `Protocol`
class) instead of requiring string keys everywhere.

## Acceptance Criteria

- [ ] `ask(SomeFunc)` works when `ctx.env` contains `{SomeFunc: impl}`.
- [ ] `Local({SomeFunc: impl}, sub_program)` accepts non-string keys in `env_update`.
- [ ] Unhashable keys fail fast with a clear `TypeError`.
- [ ] Reserved string keys (e.g., `"__interpreter__"`, `"__resolver__"`, `"__repr_limit__"`) keep
      working unchanged.
- [ ] Public types + stubs reflect the new key type (no new `type: ignore` required for env usage).
- [ ] Regression tests cover the new behavior.

## Implementation Notes

- Add a shared key type alias (e.g., `EnvKey = Hashable`) in a central module and reuse it in effect
  definitions and `ExecutionContext`.
- Replace `ensure_str(...)` / `ensure_env_mapping(...)` checks with a hashability check:
  - Prefer `collections.abc.Hashable` plus a defensive `hash(key)` probe for edge cases.
- Touch points (expected):
  - `doeff/effects/reader.py`: widen `AskEffect.key`, `LocalEffect.env_update`, function signatures.
  - `doeff/effects/_validators.py`: add `ensure_hashable(...)`; update `ensure_env_mapping(...)`.
  - `doeff/_types_internal.py`: widen `ExecutionContext.env` typing and helpers.
  - `doeff/core.pyi` (and any other stubs): update `ask`/`local` signatures.
  - `doeff/handlers/__init__.py`: keep behavior; adjust formatting (`{key!r}`) where useful.

## Subtasks

- [ ] Define `EnvKey` alias and adopt in Reader effect types
- [ ] Update validators to accept hashable env keys
- [ ] Update `ExecutionContext.env` typing and helpers
- [ ] Update `.pyi` stubs / exports to match
- [ ] Add tests for class/Protocol keys (Ask + Local)
- [ ] Run `uv run pytest`, `uv run pyright`, `uv run ruff check`

## Related

- Issue: [[ISSUE-CORE-402]]
- PR:

## Progress Log

### 2025-12-15
- Task created
