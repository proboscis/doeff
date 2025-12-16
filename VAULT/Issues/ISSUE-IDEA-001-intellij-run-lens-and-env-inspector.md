---
id: ISSUE-IDEA-001
title: Port VSCode Run Lens + Entrypoint Env Inspector to IntelliJ Plugin
module: idea
status: open
severity: high
related-project:
related-spec: SPEC-VSCODE-001-implicit-env-inspector
related-task:
related-feature:
created: 2025-12-16
updated: 2025-12-16
tags: [issue, ide, intellij, pycharm, vscode, lens, env]
---

# ISSUE-IDEA-001 — Port VSCode Run Lens + Entrypoint Env Inspector to IntelliJ Plugin

## Summary

We have feature parity in the VSCode plugin (`doeff-runner`) for:

- Editor "Run" buttons via CodeLens (run/debug/options + tool actions).
- Entrypoint inspector TreeView (entrypoints → actions → env chain → keys) with env inspection.

The IntelliJ/PyCharm plugin currently provides a gutter run icon + selection dialog, but it lacks:

- A CodeLens-like in-editor run lens experience.
- A dedicated entrypoint tree view (ToolWindow) and env chain/key inspection.

## Desired Outcome

- IntelliJ plugin surfaces the same UX primitives as VSCode:
  - In-editor run lens actions near Program entrypoints.
  - A ToolWindow that lists entrypoints, actions, and env chain with key inspection.
- Reuse `doeff-indexer` as the analysis backend (including `find-env-chain`).

## Acceptance Criteria

- [ ] Editor lens appears for `Program[...]` entrypoints and triggers doeff run (at least via the existing selection flow).
- [ ] ToolWindow shows entrypoints grouped by module and provides Run/Options actions.
- [ ] ToolWindow shows Environment Chain per entrypoint (via `doeff-indexer find-env-chain`).
- [ ] Env sources navigate to the defining file/line; env keys show overrides/final markers when resolvable.
- [ ] Basic caching + refresh exists to avoid blocking the UI thread.

## Notes / Direction

- IntelliJ implementation targets `ide-plugins/pycharm/` (IntelliJ Platform plugin).
- Use existing VSCode implementations as behavioral reference:
  - `ide-plugins/vscode/doeff-runner/src/extension.ts` (CodeLens + TreeView + env chain)
  - `VAULT/Specs/SPEC-VSCODE-001-implicit-env-inspector.md` (env chain/key inspection model)

## Related

- Spec: [[SPEC-VSCODE-001-implicit-env-inspector]]

