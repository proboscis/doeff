---
id: TASK-VSCODE-002
title: TreeDataProvider 基本実装
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
tags: [task, vscode, ide, treeview]
---

# TASK-VSCODE-002 — TreeDataProvider 基本実装

## Description

VSCode doeff-runner プラグインに TreeDataProvider を実装し、サイドバーにエントリーポイント一覧を表示するための基盤を作成する。

## Acceptance Criteria

- [ ] `DoeffProgramsProvider` クラスを実装（`vscode.TreeDataProvider` を実装）
- [ ] `package.json` に viewsContainers と views を追加
- [ ] 階層構造（モジュール > エントリーポイント）でデータを表示
- [ ] TreeView のリフレッシュ機能を実装
- [ ] エントリーポイントをクリックで該当ファイル/行にジャンプ

## Implementation Notes

### package.json への追加

```json
{
  "contributes": {
    "viewsContainers": {
      "activitybar": [
        {
          "id": "doeff-explorer",
          "title": "doeff",
          "icon": "$(symbol-method)"
        }
      ]
    },
    "views": {
      "doeff-explorer": [
        {
          "id": "doeff-programs",
          "name": "Programs"
        }
      ]
    }
  }
}
```

### TreeItem 構造

```typescript
// モジュール/ファイルノード
interface ModuleNode {
  type: 'module';
  path: string;
  displayName: string;
  children: EntrypointNode[];
}

// エントリーポイントノード
interface EntrypointNode {
  type: 'entrypoint';
  entry: IndexEntry;
  children: ActionNode[];
}

// アクションノード（run, kleisli, transform）
interface ActionNode {
  type: 'action';
  actionType: 'run' | 'runWithOptions' | 'kleisli' | 'transform';
  entry: IndexEntry;
  tool?: IndexEntry; // kleisli or transform の場合
}
```

### TreeDataProvider 実装

```typescript
class DoeffProgramsProvider implements vscode.TreeDataProvider<TreeNode> {
  private _onDidChangeTreeData = new vscode.EventEmitter<TreeNode | undefined>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  getTreeItem(element: TreeNode): vscode.TreeItem {
    // TreeItem を返す
  }

  getChildren(element?: TreeNode): Thenable<TreeNode[]> {
    // 子ノードを返す
  }

  refresh(): void {
    this._onDidChangeTreeData.fire(undefined);
  }
}
```

## Subtasks

- [ ] TreeNode 型定義を作成
- [ ] DoeffProgramsProvider クラスを実装
- [ ] package.json に viewsContainers/views を追加
- [ ] extension.ts で TreeView を登録
- [ ] リフレッシュコマンドを追加
- [ ] ファイルジャンプ機能を実装

## Related

- Feature: [[FEAT-VSCODE-002-entrypoint-explorer]]

## Progress Log

### 2025-12-05
- タスク作成

