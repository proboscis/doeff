---
id: TASK-IDEA-001
title: Implement IntelliJ run lens + entrypoint env inspector
module: idea
status: in-progress
priority: high
due-date:
related-project:
related-spec: SPEC-VSCODE-001-implicit-env-inspector
related-feature:
code_path: ide-plugins/pycharm/
created: 2025-12-16
updated: 2025-12-16
tags: [task, ide, intellij, pycharm, lens, env]
---

# TASK-IDEA-001 — Implement IntelliJ run lens + entrypoint env inspector

## Description

Port the VSCode `doeff-runner` "Run Lens" and "Entrypoint Inspector + Env Chain" features to the
IntelliJ/PyCharm plugin under `ide-plugins/pycharm/`.

## Acceptance Criteria

- [x] Add an editor lens (Code Vision / inlay) for `Program[...]` entrypoints with at least `Run`.
- [x] Add a Doeff ToolWindow that lists entrypoints and exposes Run/Options actions.
- [x] Add env chain retrieval (`doeff-indexer find-env-chain`) and display env sources (keys shown when available).
- [x] Provide navigation from env sources to file/line in editor.
- [ ] Add caching/state sync + richer actions (debug toggle, tool actions, key inspector UI).

## Implementation Notes

- Prefer IntelliJ Platform primitives:
  - Editor lens: `CodeVisionProvider` (or inlay hints) scoped to Python PSI.
  - Entrypoint tree: `ToolWindowFactory` + `SimpleTree`/`TreeStructure` and async refresh.
- Reuse existing runner flow (`ProgramExecutionController` + `ProgramSelectionDialog` +
  `DoEffRunConfigurationHelper`) as the execution backend initially.
- Extend `IndexerClient` with `find-env-chain` and corresponding data models.

## Subtasks

- [x] Model + query `find-env-chain` in `IndexerClient`
- [x] ToolWindow scaffolding + entrypoint listing (from index)
- [x] Env chain UI nodes (sources + keys placeholder)
- [x] Editor lens MVP (`Run` action)
- [ ] Add state sync + tool actions + key inspector UI
- [x] Build plugin artifact

## Related

- Issue: [[ISSUE-IDEA-001-intellij-run-lens-and-env-inspector]]
- Spec: [[SPEC-VSCODE-001-implicit-env-inspector]]
- Reference: `ide-plugins/vscode/doeff-runner/src/extension.ts`

## Progress Log

### 2025-12-16
- Added `Doeff` ToolWindow + env chain loader and in-editor Run lens; built with `./gradlew buildPlugin`.
