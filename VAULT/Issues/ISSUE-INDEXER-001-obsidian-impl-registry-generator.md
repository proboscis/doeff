---
id: ISSUE-INDEXER-001
title: Obsidian Implementation Registry Generator for Protocol Injections
module: INDEXER
status: open
severity: medium
related-project:
related-spec:
related-task:
related-feature:
created: 2025-12-12
updated: 2025-12-12
tags: [issue, indexer, vault, injection]
---

# ISSUE-INDEXER-001 — Obsidian Implementation Registry Generator for Protocol Injections

## Summary

We need an aggregated, queryable view of all functions that implement Protocol-based injection points
(`@impl(ProtoFn)`), including their feature/properties, so developers can quickly decide what to inject.

Currently, implementation properties live in code (docstrings/comments), but there is no way to
visualize or filter implementations across the repo in Obsidian/VAULT.

## Desired Outcome

- Auto-discover all `@impl(Protocol)` implementations in a target codebase.
- Extract lightweight metadata per implementation (e.g., `features`, `related-spec`, `id`, `notes`).
- Generate/refresh VAULT notes (e.g., `VAULT/References/Implementations/*.md`) with YAML frontmatter.
- Obsidian Bases/Dataview renders a table grouped/filterable by `protocol`, `features`, etc.

## Notes / Direction

- Source of truth stays in code:
  - `@impl(Protocol)` remains the canonical link from implementation → interface.
  - Metadata should be embedded in code (docstring meta block) or as kwargs to `@impl` once supported.
- Generator can be implemented as part of `doeff-indexer` (Rust) or a Python prototype first, then
  ported once the schema stabilizes.
- A `--watch` mode would enable near-real-time refresh while editing, but a pre-commit/CI refresh is
  sufficient for the first version.

## Acceptance Criteria

- [ ] CLI (or library) scans a code tree and outputs a JSON registry `{Protocol: [impls...]}`.
- [ ] CLI generates/updates VAULT implementation notes with correct frontmatter.
- [ ] Add an `Implementations.base` so Obsidian shows a live table of implementations.
- [ ] Workflow documented in VAULT (how to refresh, where metadata lives).

## Related

- Spec: (to be created) `SPEC-INDEXER-00X Code → VAULT Registry Generation`

