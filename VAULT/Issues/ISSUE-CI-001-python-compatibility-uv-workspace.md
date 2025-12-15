---
id: ISSUE-CI-001
title: Python Compatibility CI fails due to uv workspace config (doeff-indexer)
module: ci
status: closed
severity: medium
related-project:
related-spec:
related-task: TASK-CI-001
related-feature:
created: 2025-12-15
updated: 2025-12-15
tags: [issue, ci, github-actions, uv, doeff-indexer]
---

# ISSUE-CI-001 — Python Compatibility CI fails due to uv workspace config (doeff-indexer)

## Summary

`Python Compatibility` GitHub Actions workflow was consistently failing on `main` because `uv run`
could not build/install `doeff-indexer` from the workspace due to an invalid `tool.uv.workspace`
configuration, and because the compatibility matrix attempted Python versions not available on the
runner by default.

## Environment

- CI: GitHub Actions (`.github/workflows/python-compatibility.yml`)
- Tooling: `uv` via `astral-sh/setup-uv`

## Steps to Reproduce

1. Push to `main`
2. Observe `Python Compatibility` run failing at `python tools/test_python_versions.py`

## Expected Behavior

- `python tools/test_python_versions.py` succeeds for supported Python versions.

## Actual Behavior

- `uv run` fails building `doeff @ file://...` due to workspace dependency parsing:

```
Failed to parse entry for: `doeff-indexer`
Package is not included as workspace package in `tool.uv.workspace`
```

## Investigation

### Root Cause

- `pyproject.toml` excluded `packages/doeff-indexer` from the uv workspace, while the project also
  referenced a local workspace source for `doeff-indexer`, leading `uv run` to fail resolving it.
- The python compatibility matrix attempted versions (`3.13`, `3.14t`) that were not installed on
  the runner (and were not being installed as part of the workflow).

## Resolution

### Fix Applied

- `pyproject.toml`: include `doeff-indexer` as a workspace dependency via `tool.uv.sources`.
- `.github/workflows/python-compatibility.yml`: install Rust toolchain so `maturin` builds work.
- `tools/test_python_versions.py`: limit default matrix to 3.10–3.12.

### Verification

- GitHub Actions `Python Compatibility` is green on `main` after commit `bdd2b6f`.

## Related

- Task: [[TASK-CI-001]]
