---
id: TASK-VSCODE-007
title: EnvChainNode TreeView 統合
module: vscode
status: completed
priority: high
due-date:
related-project: PROJECT-VSCODE-001
related-spec: SPEC-VSCODE-001
related-feature:
code_path: ide-plugins/vscode/doeff-runner/src/
created: 2025-12-11
updated: 2025-12-11
tags: [task, vscode, ide, treeview, env]
---

# TASK-VSCODE-007 — EnvChainNode TreeView 統合

## Description

VSCode doeff-runner の TreeView に Environment Chain ノードを追加し、各 Program エントリーポイントがどの暗黙的環境をロードするかを階層的に表示する。

## Acceptance Criteria

- [x] 各 Program エントリーポイントの下に「📦 Environment」ノードが表示される
- [x] Environment ノードは折りたたみ可能（デフォルト: 折りたたみ）
- [x] 各 env ソース（~/.doeff.py, project envs）がサブノードとして表示
- [x] 各 env ノードの下にキー一覧が表示される
- [x] オーバーライドされたキーには ⚠️↓ マーカーが表示される
- [x] 最終値を持つキーには ★ マーカーが表示される
- [x] env ノードをクリックで該当ファイル/行にジャンプ

## Implementation Notes

### TreeView 構造

```
doeff Programs
├── src.features.auth
│   └── login_program: Program[User]
│       ├── ▶ Run
│       ├── ▶⚙ Options
│       └── 📦 Environment (7 keys, 4 sources) ▼
│           ├── 🏠 ~/.doeff.py ▶
│           │   └── 🔑 log_level = "DEBUG" ⚠️↓ overridden by base_env
│           │   └── 🔑 user = "john" ★
│           ├── 📄 src.base_env ▼
│           │   └── 🔑 db_host = "localhost" ★
│           │   └── 🔑 timeout = 10 ⚠️↓ overridden by features_env
│           └── ...
```

### 型定義

```typescript
interface EnvChainEntry {
  qualifiedName: string;
  filePath: string;
  line: number;
  keys: string[];
  staticValues?: Record<string, unknown>;
  isUserConfig?: boolean;
}

interface EnvChainNode extends TreeNode {
  type: 'envChain';
  entries: EnvChainEntry[];
}

interface EnvSourceNode extends TreeNode {
  type: 'envSource';
  entry: EnvChainEntry;
}

interface EnvKeyNode extends TreeNode {
  type: 'envKey';
  key: string;
  value: unknown | null;
  isFinal: boolean;           // true = ★
  overriddenBy?: string;      // ⚠️↓ の対象
}
```

### EnvChainProvider 実装

```typescript
class EnvChainProvider {
  async getEnvChain(entrypoint: IndexEntry): Promise<EnvChainEntry[]> {
    const indexerPath = await locateIndexer();
    const result = await queryIndexer(indexerPath, 'find-env-chain', {
      root: workspacePath,
      program: entrypoint.qualifiedName
    });
    return result.envChain;
  }
}
```

## Subtasks

- [x] EnvChainEntry, EnvChainNode, EnvSourceNode, EnvKeyNode 型定義
- [x] EnvChainProvider クラス実装（indexer 呼び出し）
- [x] DoeffProgramsProvider に env chain ノードを追加
- [x] オーバーライド判定ロジック実装（★ / ⚠️↓ 表示）
- [x] 折りたたみ状態管理
- [x] ファイルジャンプ機能（env ソースノード用）

## Related

- Spec: [[SPEC-VSCODE-001-implicit-env-inspector]]
- Depends on: [[TASK-VSCODE-006-indexer-env-chain]]

## Progress Log

### 2025-12-11
- タスク作成
