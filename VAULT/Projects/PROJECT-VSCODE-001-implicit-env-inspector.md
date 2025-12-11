---
id: PROJECT-VSCODE-001
title: Implicit Environment Inspector
status: pending
owner:
start-date:
target-date:
tags: [project, vscode, env, inspector]
---

# PROJECT-VSCODE-001 — Implicit Environment Inspector

## 1. Summary

VSCode doeff-runner プラグインに暗黙的環境 (implicit env) のインスペクション機能を追加する。Program エントリーポイントがどのような環境チェーンで実行されるかを可視化し、特定の key に対してどの値がロードされるかをクエリできるようにする。

### Goals

1. Env chain を TreeView で可視化（~/.doeff.py + プロジェクト env）
2. Key ごとのオーバーライドチェーンを表示（★ final / ⚠️↓ overridden）
3. 単一の `ask(key)` を実行して実際のランタイム値を取得
4. 折りたたみ可能な UI で必要な時だけ詳細を確認

## 2. Scope（範囲）

### In Scope

- doeff-indexer に `find-env-chain` コマンド追加
- VSCode TreeView に Environment Chain ノード追加
- Key Inspector (QuickPick ベース)
- Per-env refresh / Per-key resolve 機能
- ホバー情報表示

### Out of Scope

- LSP-based key autocomplete（将来検討）
- 複数ワークスペースの同時管理
- リモート開発環境での動作保証

## 3. Related Spec

- [[SPEC-VSCODE-001-implicit-env-inspector]]

## 4. Related Tasks

### Phase 1: Indexer Extension

- [[TASK-VSCODE-006-indexer-env-chain]] — Indexer find-env-chain コマンド実装

### Phase 2: VSCode TreeView Integration

- [[TASK-VSCODE-007-env-chain-treeview]] — EnvChainNode TreeView 統合

### Phase 3: Key Inspector

- [[TASK-VSCODE-008-key-inspector]] — Key Inspector 実装

### Phase 4: Polish

- [[TASK-VSCODE-009-env-inspector-polish]] — Env Inspector ポリッシュ

## 5. Related Issues

- N/A

## 6. Metrics / Progress

| Status | Count |
|--------|-------|
| 計画済み | 4 |
| 進行中 | 0 |
| 完了 | 0 |

### Task Progress

| Task | Description | Status |
|------|-------------|--------|
| TASK-VSCODE-006 | Indexer find-env-chain | ⏳ pending |
| TASK-VSCODE-007 | EnvChainNode TreeView | ⏳ pending |
| TASK-VSCODE-008 | Key Inspector | ⏳ pending |
| TASK-VSCODE-009 | Env Inspector polish | ⏳ pending |

## 7. Risks / Blockers

- **Risk 1**: `Program.pure({...})` 以外のパターンで静的解析が困難
  - Mitigation: 動的値は `<dynamic>` と表示し、per-key resolve で対応
- **Risk 2**: Env が `Program[dict]` のため、キー一覧取得に実行が必要
  - Mitigation: Per-env refresh ボタンで明示的に実行

## 8. Changelog

### 2025-12-11
- プロジェクト作成
- Spec, Tasks 作成済み
