---
id: TASK-VSCODE-003
title: エントリーポイント Indexer 連携
module: vscode
status: done
priority: high
due-date: 
related-project: 
related-spec: 
related-feature: FEAT-VSCODE-002
code_path: ide-plugins/vscode/doeff-runner/src/
created: 2025-12-05
updated: 2025-12-05
tags: [task, vscode, ide, indexer]
---

# TASK-VSCODE-003 — エントリーポイント Indexer 連携

## Description

doeff-indexer の `index` コマンドを使用してワークスペース全体の Program エントリーポイントを取得し、TreeView に表示するためのデータ層を実装する。

## Acceptance Criteria

- [ ] ワークスペース全体のエントリーポイントを indexer から取得
- [ ] エントリーポイントをモジュール/ファイル単位でグルーピング
- [ ] キャッシュ機構を実装（重複クエリを防止）
- [ ] ファイル変更時の自動リフレッシュ
- [ ] 手動リフレッシュコマンド

## Implementation Notes

### Indexer コマンド

```bash
# ワークスペース全体をインデックス
doeff-indexer index --root <workspace_path>

# 特定ファイルのみ
doeff-indexer index --root <workspace_path> --file <file_path>
```

### 出力形式

```json
{
  "version": "0.1.2",
  "root": "/path/to/workspace",
  "generated_at": "2025-12-05T...",
  "entries": [
    {
      "name": "my_program",
      "qualified_name": "src.module_a.my_program",
      "file_path": "/path/to/src/module_a.py",
      "line": 42,
      "categories": ["returns_program"],
      "markers": ["entrypoint"],
      "type_usages": [...]
    }
  ],
  "stats": {...}
}
```

### エントリーポイントのフィルタリング

`categories` に基づいてエントリーポイントをフィルタリング:
- `returns_program`: Program を返す関数
- `has_marker`: `# doeff: entrypoint` などのマーカー付き

```typescript
function isEntrypoint(entry: IndexEntry): boolean {
  // entrypoint マーカーがあるか、Program を返す関数
  return entry.markers?.includes('entrypoint') ||
         entry.categories.includes('returns_program');
}
```

### キャッシュ戦略

```typescript
interface IndexCache {
  timestamp: number;
  entries: Map<string, IndexEntry[]>; // file_path -> entries
}

class EntrypointIndexer {
  private cache: IndexCache | null = null;
  private readonly CACHE_TTL_MS = 30000;

  async getEntrypoints(rootPath: string): Promise<IndexEntry[]> {
    if (this.cache && Date.now() - this.cache.timestamp < this.CACHE_TTL_MS) {
      return this.flattenCache();
    }
    return this.refreshIndex(rootPath);
  }

  invalidateFile(filePath: string): void {
    // 特定ファイルのキャッシュを無効化
  }
}
```

### ファイル変更監視

```typescript
// extension.ts
const watcher = vscode.workspace.createFileSystemWatcher('**/*.py');
watcher.onDidChange(uri => indexer.invalidateFile(uri.fsPath));
watcher.onDidCreate(uri => indexer.invalidateFile(uri.fsPath));
watcher.onDidDelete(uri => indexer.invalidateFile(uri.fsPath));
```

## Subtasks

- [ ] EntrypointIndexer クラスを実装
- [ ] キャッシュ機構を実装
- [ ] ファイル変更監視を追加
- [ ] エントリーポイントのグルーピングロジック
- [ ] リフレッシュコマンドの登録

## Related

- Feature: [[FEAT-VSCODE-002-entrypoint-explorer]]
- Task: [[TASK-VSCODE-002-treeview-provider]]

## Progress Log

### 2025-12-05
- タスク作成

