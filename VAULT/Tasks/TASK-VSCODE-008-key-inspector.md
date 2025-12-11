---
id: TASK-VSCODE-008
title: Key Inspector 実装
module: vscode
status: pending
priority: medium
due-date:
related-project: PROJECT-VSCODE-001
related-spec: SPEC-VSCODE-001
related-feature:
code_path: ide-plugins/vscode/doeff-runner/src/
created: 2025-12-11
updated: 2025-12-11
tags: [task, vscode, ide, inspector, env]
---

# TASK-VSCODE-008 — Key Inspector 実装

## Description

VSCode doeff-runner に Key Inspector 機能を追加し、任意の env キーに対してオーバーライドチェーンを表示し、ランタイム値を取得できるようにする。

## Acceptance Criteria

- [ ] QuickPick ベースの Key Inspector が起動できる
- [ ] キー入力でオーバーライドチェーンを表示
- [ ] 静的解析で取得した最終値を表示
- [ ] 各キーに「[▶ resolve]」ボタンで `ask(key)` をランタイム実行
- [ ] 各 env に「[🔄 refresh keys]」ボタンで env Program を実行してキー一覧を更新
- [ ] 動的な値は `<dynamic>` と表示され、resolve で実値を取得
- [ ] エラー時はスタックトレースを表示

## Implementation Notes

### QuickPick Inspector UI

```
> Query env key for login_program
┌────────────────────────────────────────────┐
│ 🔍 Enter key name: timeout                  │
├────────────────────────────────────────────┤
│ $(check) timeout                            │
│   Final: 30 (from auth_env)                │
│   Chain: base_env(10) → features_env(20) → auth_env(30) │
│                                            │
│ $(play) Run ask("timeout")                  │
│ $(copy) Copy final value                    │
└────────────────────────────────────────────┘
```

### Two-level Refresh Mechanism

1. **[🔄 refresh keys]** (per-env)
   - Env は `Program[dict]` なので実行が必要
   - env Program を実行してキー一覧を取得
   - TreeView のキーリストを更新

2. **[▶ resolve]** (per-key)
   - `ask(key)` を実行してランタイム値を取得
   - `<dynamic>` 表示が実際の値に置き換わる

### Runtime Ask Execution

```python
# 内部的に生成される Program
from doeff import do, ask

@do
def __doeff_inspect_key():
    """Internal: Inspect single key value."""
    value = yield ask("{key}")
    return value
```

### 型定義

```typescript
interface KeyResolution {
  key: string;
  finalValue: unknown | null;
  chain: Array<{
    envQualifiedName: string;
    value: unknown | null;
    isOverridden: boolean;
  }>;
  runtimeValue?: unknown;
  runtimeError?: string;
}
```

### Commands

| Command | Description |
|---------|-------------|
| `doeff-runner.inspectEnvKey` | Open key inspector for entrypoint |
| `doeff-runner.refreshEnvKeys` | Run env Program to discover available keys |
| `doeff-runner.resolveEnvKey` | Resolve single key by running `ask(key)` at runtime |

## Subtasks

- [ ] KeyResolution 型定義
- [ ] EnvInspectorPanel クラス実装
- [ ] QuickPick UI 実装
- [ ] キー解決ロジック実装（静的分析）
- [ ] ランタイム ask 実行機能
- [ ] 「refresh keys」機能（env Program 実行）
- [ ] コマンド登録（inspectEnvKey, refreshEnvKeys, resolveEnvKey）
- [ ] TreeView の「[▶ resolve]」「[🔄 refresh keys]」ボタン実装

## Related

- Spec: [[SPEC-VSCODE-001-implicit-env-inspector]]
- Depends on: [[TASK-VSCODE-007-env-chain-treeview]]

## Progress Log

### 2025-12-11
- タスク作成
