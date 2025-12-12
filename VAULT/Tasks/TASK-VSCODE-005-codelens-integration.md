---
id: TASK-VSCODE-005
title: CodeLens 連携実装
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
tags: [task, vscode, ide, codelens]
---

# TASK-VSCODE-005 — CodeLens 連携実装

## Description

TreeView で選択したアクションをエディタの CodeLens に反映し、TreeView と CodeLens の状態を同期する。

## Acceptance Criteria

- [ ] TreeView で選択したデフォルトアクションが CodeLens に表示される
- [ ] CodeLens からの実行結果が TreeView に反映される
- [ ] 双方向の状態同期が正しく動作する
- [ ] TreeView で選択したエントリーポイントがエディタでハイライトされる

## Implementation Notes

### 状態共有

TreeView と CodeLens で状態を共有するための中央ストアを実装:

```typescript
class DoeffStateStore {
  private _onStateChange = new vscode.EventEmitter<void>();
  readonly onStateChange = this._onStateChange.event;

  private preferences: Map<string, ActionType> = new Map();

  setDefaultAction(qualifiedName: string, action: ActionType): void {
    this.preferences.set(qualifiedName, action);
    this._onStateChange.fire();
  }

  getDefaultAction(qualifiedName: string): ActionType | undefined {
    return this.preferences.get(qualifiedName);
  }
}
```

### CodeLens の更新

TreeView での選択変更時に CodeLens を更新:

```typescript
// extension.ts
const stateStore = new DoeffStateStore();

// TreeView からの変更を CodeLens に反映
stateStore.onStateChange(() => {
  codeLensProvider.refresh();
});

// CodeLens での表示を更新
class ProgramCodeLensProvider {
  provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
    const lenses: vscode.CodeLens[] = [];
    
    for (const decl of extractProgramDeclarations(document)) {
      const qualifiedName = getQualifiedName(document, decl);
      const defaultAction = stateStore.getDefaultAction(qualifiedName);
      
      if (defaultAction) {
        // デフォルトアクションを先頭に表示
        lenses.push(this.createActionLens(decl, defaultAction, '★'));
      }
      
      // 標準のボタンも表示
      lenses.push(/* Run */);
      lenses.push(/* Run with options */);
      // ...
    }
    
    return lenses;
  }
}
```

### TreeView でのハイライト

エディタでカーソル位置のエントリーポイントを TreeView でハイライト:

```typescript
// エディタのカーソル移動を監視
vscode.window.onDidChangeTextEditorSelection(event => {
  const document = event.textEditor.document;
  const line = event.selections[0].active.line;
  
  const declaration = findDeclarationAtLine(document, line);
  if (declaration) {
    const qualifiedName = getQualifiedName(document, declaration);
    treeView.reveal(findNodeByQualifiedName(qualifiedName), {
      select: true,
      focus: false,
    });
  }
});
```

### エントリーポイントへのジャンプ

TreeView からエディタへのジャンプ:

```typescript
async function revealEntrypoint(entry: IndexEntry): Promise<void> {
  const uri = vscode.Uri.file(entry.filePath);
  const document = await vscode.workspace.openTextDocument(uri);
  const editor = await vscode.window.showTextDocument(document);
  
  const position = new vscode.Position(entry.line - 1, 0);
  editor.selection = new vscode.Selection(position, position);
  editor.revealRange(
    new vscode.Range(position, position),
    vscode.TextEditorRevealType.InCenter
  );
}
```

### 双方向同期のシーケンス

```
TreeView で選択
    ↓
StateStore.setDefaultAction()
    ↓
onStateChange イベント発火
    ↓
CodeLensProvider.refresh()
    ↓
CodeLens が更新される

CodeLens で実行
    ↓
実行結果をログ
    ↓
TreeView のステータスアイコンを更新（オプション）
```

## Subtasks

- [ ] DoeffStateStore を実装
- [ ] CodeLensProvider に状態連携を追加
- [ ] TreeView からの選択を CodeLens に反映
- [ ] エディタ位置と TreeView の同期
- [ ] エントリーポイントへのジャンプ機能

## Related

- Feature: [[FEAT-VSCODE-002-entrypoint-explorer]]
- Task: [[TASK-VSCODE-002-treeview-provider]]
- Task: [[TASK-VSCODE-004-action-selector]]

## Progress Log

### 2025-12-05
- タスク作成

