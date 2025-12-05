---
id: TASK-VSCODE-001
title: Kleisli CodeLens ボタン実装
module: vscode
status: done
priority: high
due-date: 
related-project: 
related-spec: 
related-feature: FEAT-VSCODE-001
code_path: ide-plugins/vscode/doeff-runner/src/
created: 2025-12-05
updated: 2025-12-05
tags: [task, vscode, ide, kleisli, codelens]
---

# TASK-VSCODE-001 — Kleisli CodeLens ボタン実装

## Description

VSCode doeff-runner プラグインの ProgramCodeLensProvider を拡張し、doeff-indexer が返す Kleisli tool ごとにボタンを追加表示する。

## Acceptance Criteria

- [ ] `provideCodeLenses` を非同期化して Kleisli 情報を取得
- [ ] 各 Kleisli に対応する CodeLens ボタンを追加
- [ ] `doeff-runner.runWithKleisli` コマンドを追加
- [ ] package.json にコマンド定義を追加
- [ ] キャッシュを活用して高速なボタン表示を実現
- [ ] デフォルト interpreter の自動選択

## Implementation Notes

### 変更点

1. **CodeLensProvider の非同期化**
   - `provideCodeLenses` の戻り値を `Promise<CodeLens[]>` または `Thenable<CodeLens[]>` に変更
   - 各 Program declaration に対して `find-kleisli` を呼び出し

2. **新規コマンド追加**
   - `doeff-runner.runWithKleisli`: kleisli を指定して実行
   - 引数: `uri`, `lineNumber`, `kleisliQualifiedName`

3. **UI 改善**
   - Kleisli ボタンには識別しやすいプレフィックス（例: `🔧` または `+`）を追加
   - ボタンテキストは Kleisli の短い名前を使用

### コード構造

```typescript
// 新しいコマンド引数
interface KleisliRunArgs {
  uri: vscode.Uri;
  lineNumber: number;
  kleisliQualifiedName: string;
  interpreterQualifiedName: string;
}

// provideCodeLenses を async に
async provideCodeLenses(document: vscode.TextDocument): Promise<vscode.CodeLens[]> {
  const lenses: vscode.CodeLens[] = [];
  
  for (const decl of extractProgramDeclarations(document)) {
    // 既存のボタン
    lenses.push(/* Run */);
    lenses.push(/* Run with options */);
    
    // Kleisli ボタンを追加
    const kleisliTools = await fetchKleisliForType(decl.typeArg);
    for (const kleisli of kleisliTools) {
      lenses.push(new vscode.CodeLens(decl.range, {
        title: `+ ${kleisli.name}`,
        command: 'doeff-runner.runWithKleisli',
        arguments: [document.uri, decl.range.start.line, kleisli.qualifiedName]
      }));
    }
  }
  
  return lenses;
}
```

## Subtasks

- [x] `provideCodeLenses` を非同期化
- [x] Kleisli 取得ロジックを追加
- [x] `runWithKleisli` コマンドを実装
- [x] package.json 更新
- [x] コンパイル・リント通過

## Related

- Feature: [[FEAT-VSCODE-001-kleisli-buttons]]

## Progress Log

### 2025-12-05
- タスク作成
- 実装開始
- `ProgramCodeLensProvider` を非同期化、Kleisli キャッシュ追加
- `runWithKleisli` コマンド実装
- `package.json` にコマンド定義追加
- コンパイル・リント通過
- タスク完了


