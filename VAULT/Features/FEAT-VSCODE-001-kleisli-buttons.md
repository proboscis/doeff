---
id: FEAT-VSCODE-001
title: Kleisli Tool Buttons for doeff-runner
module: vscode
status: done
created: 2025-12-05
updated: 2025-12-05
priority: high
tags: [feature, vscode, ide, kleisli]
---

# FEAT-VSCODE-001 — Kleisli Tool Buttons for doeff-runner

## 1. Summary

VSCode の doeff-runner プラグインを拡張し、Program entrypoint に対して doeff-indexer が返す各 Kleisli tool のボタンを表示する。ユーザーはボタンをクリックするだけで、その Kleisli を適用した状態で entrypoint を実行できる。

現在は `Run | Run with options` の2つのボタンしかないが、これを拡張して各 Kleisli tool に対応したボタンを動的に追加する。

## 2. Goals / Non-Goals

**Goals:**
- Program entrypoint に対して、doeff-indexer から返される Kleisli tool ごとにボタンを表示
- ボタンクリックで該当 Kleisli を `--apply` オプションとして実行
- デフォルト interpreter を自動選択（最初の interpreter を使用）
- 使いやすい UX の提供

**Non-Goals:**
- Kleisli の組み合わせ選択（複数 Kleisli の同時適用）
- Transformer の動的ボタン追加（将来の拡張として検討）
- インタープリタの動的選択ボタン

## 3. Linked Specs

- N/A

## 4. Linked Designs

- N/A

## 5. Tasks

- [[TASK-VSCODE-001-kleisli-codelens]]

## 6. Related Decisions

- N/A

## 7. Related Issues

- N/A

## 8. Acceptance Criteria

- [ ] Program entrypoint 行に `Run | Run with options` に加えて Kleisli tool 名のボタンが表示される
- [ ] Kleisli ボタンをクリックすると、デフォルト interpreter + 該当 Kleisli で実行される
- [ ] Kleisli がない場合は従来通り `Run | Run with options` のみ表示
- [ ] ボタン表示が高速（キャッシュ活用）
- [ ] エラー時は適切なメッセージを表示

## 9. Notes / References

### 現在の実装

`extension.ts` の `ProgramCodeLensProvider` が CodeLens を提供:

```typescript
provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
  const lenses: vscode.CodeLens[] = [];
  for (const decl of extractProgramDeclarations(document)) {
    lenses.push(
      new vscode.CodeLens(decl.range, {
        title: 'Run',
        command: 'doeff-runner.runDefault',
        arguments: [document.uri, decl.range.start.line]
      })
    );
    lenses.push(
      new vscode.CodeLens(decl.range, {
        title: 'Run with options',
        command: 'doeff-runner.runOptions',
        arguments: [document.uri, decl.range.start.line]
      })
    );
  }
  return lenses;
}
```

### 拡張後のイメージ

```
my_program: Program[int] = ...
Run | Run with options | 🔧 with_logging | 🔧 with_tracing | 🔧 with_retry
```


