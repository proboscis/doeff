---
id: TASK-VSCODE-009
title: Env Inspector ポリッシュ
module: vscode
status: pending
priority: low
due-date:
related-project:
related-spec: SPEC-VSCODE-001
related-feature:
code_path: ide-plugins/vscode/doeff-runner/src/
created: 2025-12-11
updated: 2025-12-11
tags: [task, vscode, ide, polish, env]
---

# TASK-VSCODE-009 — Env Inspector ポリッシュ

## Description

Env Inspector 機能の UX を改善し、ホバー情報、静的解析精度の向上、設定オプションを追加する。

## Acceptance Criteria

- [ ] Program 変数へのホバーで env chain サマリーを表示
- [ ] `Program.pure({...})` の静的解析精度向上
- [ ] 設定オプション（タイムアウト、~/.doeff.py 表示切り替え等）
- [ ] パフォーマンス最適化（キャッシュ）

## Implementation Notes

### Hover Information

Program 変数にホバーした際に env 情報を表示:

```
login_program: Program[User]

Environment Chain:
• ~/.doeff.py (2 keys)
• base_env (3 keys)
• features_env (2 keys)
• auth_env (4 keys)

Click to inspect environment →
```

### 設定オプション

```json
{
  "doeff-runner.envInspector.showUserConfig": true,
  "doeff-runner.envInspector.askTimeout": 10000,
  "doeff-runner.envInspector.cacheEnvChain": true,
  "doeff-runner.envInspector.defaultCollapsed": true
}
```

### 静的解析精度向上

対応パターンの拡張:
- `Program.pure({...})` - 基本パターン
- `Program.pure(dict(...))` - dict() 形式
- `{**base, "key": value}` - マージパターン
- f-string の部分的解析

### キャッシュ戦略

- ファイル変更時に該当 env のキャッシュを無効化
- ワークスペース全体のリフレッシュ時に全キャッシュクリア

## Subtasks

- [ ] HoverProvider 実装（env chain サマリー表示）
- [ ] 設定スキーマ定義と package.json への追加
- [ ] 静的解析パターンの拡張
- [ ] env chain キャッシュ機構実装
- [ ] ファイル変更監視とキャッシュ無効化
- [ ] ドキュメント作成

## Related

- Spec: [[SPEC-VSCODE-001-implicit-env-inspector]]
- Depends on: [[TASK-VSCODE-008-key-inspector]]

## Progress Log

### 2025-12-11
- タスク作成
