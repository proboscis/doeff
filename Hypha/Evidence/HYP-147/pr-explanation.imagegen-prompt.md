Tool: imagegen

Prompt:

Use case: infographic-diagram
Asset type: GitHub PR explanation diagram PNG for repository evidence
Primary request: Create a dense Japanese Kasumigaseki-style briefing diagram explaining HYP-147 repository hygiene update for the doeff repository. The diagram must look like a polished government-style technical brief, information-dense, with clear section hierarchy, arrows, icons/pictograms, and compact labels. All visible text must be Japanese, except exact technical identifiers where needed.
Canvas: landscape 16:9, high resolution, white/light gray paper background, navy headings, restrained accent colors, thin divider lines, small pictogram icons.
Required visible Japanese headings and labels:
- "HYP-147 リポジトリ整理"
- "目的: 生成物を追跡対象から外す"
- "標準 lint 経路"
- "doeff-linter --no-log"
- "doeff-flow 実行結果"
- ".doeff-flow/"
- "SQLite DB / WAL / SHM"
- "*.db-wal / *.db-shm"
- "*.sqlite-wal / *.sqlite-shm"
- "ローカル agent 状態"
- ".agent-home/"
- ".playwright-mcp/"
- "再発検査"
- "make check-repo-hygiene"
- "git ls-files"
- "PR #416"
- "確認観点: 意図した fixture だけを許可"
Content structure: left column shows prior problem: normal lint and examples produced tracked logs, trace, SQLite DB, WAL/SHM sidecars, local agent/browser state. Center column shows implementation: Makefile uses doeff-linter --no-log; .gitignore ignores logs, .doeff-flow, SQLite DB plus WAL/SHM sidecars, local agent/browser artifacts; tracked generated files removed. Right column shows review checks: make check-repo-hygiene scans git ls-files for None, Untitled, server.js, JSONL, DB, SQLite, WAL/SHM, agent state, browser capture; unexpected tracked artifacts fail. Use arrows from problem to implementation to verification. Add small icons for lint document, database cylinder, trace lines, folder, shield/check.
Constraints: no pseudo-code beyond the exact identifiers listed; do not mention Program.resolve, lazy_ask, runtime config, UI evidence, or unrelated APIs. Avoid English prose. Ensure text is legible and Japanese-first.
