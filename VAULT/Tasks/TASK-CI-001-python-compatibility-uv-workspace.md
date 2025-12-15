---
id: TASK-CI-001
title: Fix Python Compatibility CI (uv workspace + version matrix)
module: ci
status: done
priority: high
due-date:
related-project:
related-spec:
related-feature:
code_path: .github/workflows/python-compatibility.yml
created: 2025-12-15
updated: 2025-12-15
tags: [task, ci, github-actions, uv]
---

# TASK-CI-001 — Fix Python Compatibility CI (uv workspace + version matrix)

## Description

Unbreak `Python Compatibility` workflow by making `doeff-indexer` resolvable via `uv` and aligning
the checked Python version matrix with supported versions.

## Acceptance Criteria

- [x] `python tools/test_python_versions.py` succeeds on GitHub Actions.
- [x] `doeff-indexer` is resolvable/buildable under `uv run` in CI.
- [x] Matrix covers supported versions (3.10–3.12).

## Implementation Notes

- Ensure `doeff-indexer` is treated as a uv workspace member/source (`tool.uv.sources`).
- Install Rust toolchain in CI for `maturin` / native extension builds.

## Subtasks

- [x] Fix uv workspace/source config for `doeff-indexer`
- [x] Install Rust toolchain in `python-compatibility.yml`
- [x] Update `tools/test_python_versions.py` default versions

## Related

- Issue: [[ISSUE-CI-001]]
- PR:

## Progress Log

### 2025-12-15
- Fixed and verified in commit `bdd2b6f`
