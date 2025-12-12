---
id: TASK-VSCODE-004
title: アクション選択 UI 実装
module: vscode
status: done
priority: medium
due-date: 
related-project: 
related-spec: 
related-feature: FEAT-VSCODE-002
code_path: ide-plugins/vscode/doeff-runner/src/
created: 2025-12-05
updated: 2025-12-05
tags: [task, vscode, ide, action-selector]
---

# TASK-VSCODE-004 — アクション選択 UI 実装

## Description

各エントリーポイントに対して run/run_with_options/kleisli/transform を選択するための UI を実装し、選択状態を永続化する。

## Acceptance Criteria

- [ ] エントリーポイントのコンテキストメニューにアクション選択を追加
- [ ] TreeView 内でアクションを子ノードとして表示
- [ ] アクションをクリックで即座に実行
- [ ] 選択状態を workspaceState に永続化
- [ ] 利用可能な kleisli/transform を動的に取得

## Implementation Notes

### アクションノードの表示

各エントリーポイントの下に利用可能なアクションを表示:

```
├── my_program: Program[int]
│   ├── ▶ Run (default)
│   ├── ▶⚙ Run with options
│   ├── 🔗 with_logging (kleisli)
│   ├── 🔗 with_retry (kleisli)
│   └── 🔀 traced (transform)
```

### アクションタイプ

```typescript
type ActionType = 
  | { kind: 'run' }
  | { kind: 'runWithOptions' }
  | { kind: 'kleisli'; tool: IndexEntry }
  | { kind: 'transform'; tool: IndexEntry };

interface ActionNode {
  type: 'action';
  actionType: ActionType;
  parentEntry: IndexEntry;
}
```

### コンテキストメニュー

package.json への追加:

```json
{
  "contributes": {
    "menus": {
      "view/item/context": [
        {
          "command": "doeff-runner.runFromTree",
          "when": "view == doeff-programs && viewItem == entrypoint",
          "group": "inline"
        },
        {
          "command": "doeff-runner.setDefaultAction",
          "when": "view == doeff-programs && viewItem == action",
          "group": "navigation"
        }
      ]
    }
  }
}
```

### 選択状態の永続化

```typescript
interface ActionPreference {
  entrypointQualifiedName: string;
  defaultAction: ActionType;
}

class ActionPreferenceStore {
  constructor(private context: vscode.ExtensionContext) {}

  getPreference(qualifiedName: string): ActionType | undefined {
    const prefs = this.context.workspaceState.get<ActionPreference[]>('actionPreferences', []);
    return prefs.find(p => p.entrypointQualifiedName === qualifiedName)?.defaultAction;
  }

  setPreference(qualifiedName: string, action: ActionType): Thenable<void> {
    const prefs = this.context.workspaceState.get<ActionPreference[]>('actionPreferences', []);
    const updated = prefs.filter(p => p.entrypointQualifiedName !== qualifiedName);
    updated.push({ entrypointQualifiedName: qualifiedName, defaultAction: action });
    return this.context.workspaceState.update('actionPreferences', updated);
  }
}
```

### Kleisli/Transform の動的取得

```typescript
async function getAvailableActions(entry: IndexEntry, rootPath: string): Promise<ActionNode[]> {
  const actions: ActionNode[] = [
    { type: 'action', actionType: { kind: 'run' }, parentEntry: entry },
    { type: 'action', actionType: { kind: 'runWithOptions' }, parentEntry: entry },
  ];

  const typeArg = extractTypeArg(entry);
  
  // Kleisli を取得
  const kleisliTools = await fetchEntries(indexerPath, rootPath, 'find-kleisli', typeArg);
  for (const tool of kleisliTools) {
    actions.push({
      type: 'action',
      actionType: { kind: 'kleisli', tool },
      parentEntry: entry,
    });
  }

  // Transform を取得
  const transformTools = await fetchEntries(indexerPath, rootPath, 'find-transforms', typeArg);
  for (const tool of transformTools) {
    actions.push({
      type: 'action',
      actionType: { kind: 'transform', tool },
      parentEntry: entry,
    });
  }

  return actions;
}
```

## Subtasks

- [ ] ActionNode 型定義を追加
- [ ] アクションノードの TreeItem 生成
- [ ] コンテキストメニューを package.json に追加
- [ ] ActionPreferenceStore を実装
- [ ] Kleisli/Transform 取得ロジックを TreeProvider に統合
- [ ] アクション実行コマンドを追加

## Related

- Feature: [[FEAT-VSCODE-002-entrypoint-explorer]]
- Task: [[TASK-VSCODE-002-treeview-provider]]
- Task: [[TASK-VSCODE-003-entrypoint-indexing]]

## Progress Log

### 2025-12-05
- タスク作成

