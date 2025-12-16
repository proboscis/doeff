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

- [ ] Add an editor lens (Code Vision / inlay) for `Program[...]` entrypoints with at least `Run`.
- [ ] Add a Doeff ToolWindow that lists entrypoints and exposes Run/Options actions.
- [ ] Add env chain retrieval (`doeff-indexer find-env-chain`) and display env sources + keys.
- [ ] Provide navigation from env sources to file/line in editor.
- [ ] Keep indexing/env queries off the UI thread; add caching + explicit refresh.

## Implementation Notes

- Prefer IntelliJ Platform primitives:
  - Editor lens: `CodeVisionProvider` (or inlay hints) scoped to Python PSI.
  - Entrypoint tree: `ToolWindowFactory` + `SimpleTree`/`TreeStructure` and async refresh.
- Reuse existing runner flow (`ProgramExecutionController` + `ProgramSelectionDialog` +
  `DoEffRunConfigurationHelper`) as the execution backend initially.
- Extend `IndexerClient` with `find-env-chain` and corresponding data models.

## Subtasks

- [ ] Model + query `find-env-chain` in `IndexerClient`
- [ ] ToolWindow scaffolding + entrypoint listing (from index)
- [ ] Env chain UI nodes (sources + keys + markers)
- [ ] Editor lens MVP (`Run` action)
- [ ] Build + smoke test in IntelliJ sandbox

## Related

- Issue: [[ISSUE-IDEA-001-intellij-run-lens-and-env-inspector]]
- Spec: [[SPEC-VSCODE-001-implicit-env-inspector]]
- Reference: `ide-plugins/vscode/doeff-runner/src/extension.ts`

## Progress Log

### 2025-12-16
- Task created; implementation started.

